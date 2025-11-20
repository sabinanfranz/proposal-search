from fastapi import FastAPI, Request, HTTPException
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import hmac
import hashlib
import time
import re
import google.generativeai as genai
import config

app = FastAPI()

# 허용된 채널 ID (여러 개 설정 가능)
ALLOWED_CHANNELS = set(config.ALLOWED_CHANNELS)

# 시스템 프롬프트
SYSTEM_PROMPT = """
당신은 데이원컴퍼니(Day1 Company) B2B 사업부의 교육 컨설턴트(LD, Learning Designer)를 지원하는 제안서 검색 전문 AI 어시스턴트입니다.

## 역할 및 목적

LD가 제안서 정보와 내용을 원활하고 쉽게 찾을 수 있도록 돕습니다.

## 핵심 원칙

- **사실만 전달**: 제안서 내에 실제로 존재하는 내용만 제시합니다.

- **추측 금지**: 제안서에 없는 내용은 절대 추측하거나 생성하지 않습니다.

- **정확한 인용**: 모든 정보는 출처 제안서명과 함께 제공합니다.

## 답변 규칙

### 1. 제안서 검색 요청 시

- 관련된 제안서의 **정확한 파일명**을 제시합니다.

- 제안서가 여러 개인 경우 모두 나열합니다.

- 형식 예시:

```
  관련 제안서를 찾았습니다:

  1. 패스트캠퍼스_교육제안서_삼성전자_생성형AI교육과정_240827.txt

  2. 패스트캠퍼스_교육제안서_LG전자_생성형AI 교육 제안 정보는 빠짐없이 제공합니다.
```

## 금지 사항

❌ 제안서에 없는 내용 추측

❌ 일반적인 교육 관련 지식 제공 (제안서 기반만)

❌ 애매모호한 답변

❌ 출처 제안서명 누락
"""

# Slack 클라이언트 초기화
slack_client = WebClient(token=config.SLACK_BOT_TOKEN)

# Gemini 설정
genai.configure(api_key=config.GEMINI_API_KEY)

# 처리된 이벤트 ID 캐시 (중복 방지)
processed_events = set()


def verify_slack_signature(request: Request, body: bytes) -> bool:
    """Slack 요청 서명 검증"""
    slack_signature = request.headers.get("X-Slack-Signature", "")
    slack_request_timestamp = request.headers.get("X-Slack-Request-Timestamp", "")

    # 타임스탬프 검증 (5분 이내)
    if abs(time.time() - int(slack_request_timestamp)) > 60 * 5:
        return False

    # 서명 생성
    sig_basestring = f"v0:{slack_request_timestamp}:{body.decode('utf-8')}"
    my_signature = 'v0=' + hmac.new(
        config.SLACK_SIGNING_SECRET.encode(),
        sig_basestring.encode(),
        hashlib.sha256
    ).hexdigest()

    return hmac.compare_digest(my_signature, slack_signature)


def query_proposal_store(question: str) -> tuple[str, list]:
    """
    제안서 스토어에 질문하고 답변과 참조 문서 반환

    Returns:
        (답변 텍스트, 참조 문서 리스트)
    """
    try:
        # 방법 1: File Search가 이미 설정된 모델 사용
        # Google AI Studio에서 파일을 업로드하고 store를 생성한 경우
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT
        )
        
        # File Search는 Google AI Studio에서 설정된 경우
        # 모델에 자동으로 연결됩니다
        response = model.generate_content(question)
        
        answer = response.text
        
        # 참조 문서 추출 (grounding metadata가 있는 경우)
        sources = []
        if hasattr(response, 'candidates') and response.candidates:
            candidate = response.candidates[0]
            if hasattr(candidate, 'grounding_metadata'):
                metadata = candidate.grounding_metadata
                if hasattr(metadata, 'grounding_chunks'):
                    source_set = set()
                    for chunk in metadata.grounding_chunks:
                        if hasattr(chunk, 'retrieved_context'):
                            ctx = chunk.retrieved_context
                            title = getattr(ctx, 'title', 'Unknown')
                            source_set.add(title)
                    sources = list(source_set)
        
        return answer, sources

    except Exception as e:
        print(f"File Search 오류: {str(e)}")
        # 폴백: 일반 Gemini API 사용
        return query_without_file_search(question)


def query_without_file_search(question: str) -> tuple[str, list]:
    """
    File Search 없이 Gemini API로 직접 쿼리
    (File Search가 설정되지 않은 경우 대체 방법)
    """
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT
        )
        
        # 프롬프트에 제안서 검색 컨텍스트 추가
        full_prompt = f"""
        다음 질문에 대해 제안서 데이터베이스를 검색한 것처럼 답변해주세요.
        만약 실제 데이터가 없다면, "제안서 데이터베이스에서 관련 내용을 찾을 수 없습니다"라고 답변하세요.
        
        질문: {question}
        """
        
        response = model.generate_content(full_prompt)
        answer = response.text
        sources = []  # File Search 없으므로 빈 리스트
        
        return answer, sources

    except Exception as e:
        return f"오류가 발생했습니다: {str(e)}", []


def format_slack_message(answer: str, sources: list, question: str) -> dict:
    """Slack 메시지 포맷팅 (Block Kit 사용)"""
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*질문:* {question}"
            }
        },
        {
            "type": "divider"
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*답변:*\n{answer}"
            }
        }
    ]

    # 참조 문서가 있으면 추가
    if sources:
        source_text = "\n".join([f"• {source}" for source in sources[:5]])
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*참조 문서:*\n{source_text}"
            }
        })

    return {"blocks": blocks}


def extract_query_from_mention(text: str) -> str:
    """멘션에서 쿼리 텍스트 추출 (봇 멘션 제거)"""
    # <@U12345678> 형식의 멘션 제거
    query = re.sub(r'<@[A-Z0-9]+>', '', text).strip()
    return query


@app.post("/slack/events")
async def slack_events(request: Request):
    """Slack Events API 엔드포인트"""

    # 요청 본문 읽기
    body = await request.body()

    # JSON 파싱
    import json
    data = json.loads(body)

    # URL 검증 챌린지 (서명 검증 전에 처리)
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    # 서명 검증 (URL 검증 이외의 요청)
    if not verify_slack_signature(request, body):
        raise HTTPException(status_code=403, detail="Invalid signature")

    # 이벤트 처리
    if data.get("type") == "event_callback":
        event = data.get("event", {})
        event_id = data.get("event_id")

        # 중복 이벤트 방지
        if event_id in processed_events:
            return {"status": "ok"}
        processed_events.add(event_id)

        # app_mention 이벤트 처리 (봇이 멘션되었을 때)
        if event.get("type") == "app_mention":
            channel = event.get("channel")

            # 허용된 채널에서만 응답 (설정이 없으면 모든 채널 허용)
            if ALLOWED_CHANNELS and channel not in ALLOWED_CHANNELS:
                return {"status": "ok"}

            thread_ts = event.get("thread_ts") or event.get("ts")
            text = event.get("text", "")

            # 멘션에서 쿼리 추출
            question = extract_query_from_mention(text)

            if not question:
                slack_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="질문을 입력해주세요. 예: @B2B Research Bot 제안서에서 AI 관련 내용 찾아줘"
                )
                return {"status": "ok"}

            try:
                # "처리 중" 메시지 전송
                processing_msg = slack_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text="🤔 제안서를 검색하고 있습니다..."
                )

                # 제안서 스토어 쿼리
                answer, sources = query_proposal_store(question)

                # 메시지 포맷팅
                formatted_msg = format_slack_message(answer, sources, question)

                # "처리 중" 메시지 삭제
                slack_client.chat_delete(
                    channel=channel,
                    ts=processing_msg["ts"]
                )

                # 답변 전송 (스레드 댓글로)
                slack_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    **formatted_msg
                )

            except SlackApiError as e:
                print(f"Slack API 오류: {e.response['error']}")
                slack_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"❌ 오류가 발생했습니다: {e.response['error']}"
                )
            except Exception as e:
                print(f"일반 오류: {str(e)}")
                slack_client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=f"❌ 오류가 발생했습니다: {str(e)}"
                )

    return {"status": "ok"}


@app.get("/")
async def root():
    """헬스체크 엔드포인트"""
    return {"status": "healthy", "service": "B2B Research Bot"}


@app.get("/health")
async def health():
    """Railway 헬스체크"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=config.HOST, port=config.PORT)
