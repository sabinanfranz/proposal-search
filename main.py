from fastapi import FastAPI, Request, HTTPException
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import hmac
import hashlib
import time
from google import genai
from google.genai import types
import config

app = FastAPI()

# Slack 클라이언트 초기화
slack_client = WebClient(token=config.SLACK_BOT_TOKEN)

# Gemini 클라이언트 초기화
gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)

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
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=question,
            config=types.GenerateContentConfig(
                tools=[
                    types.Tool(
                        file_search=types.FileSearch(
                            file_search_store_names=[config.FILE_SEARCH_STORE_NAME]
                        )
                    )
                ]
            )
        )

        answer = response.text

        # 참조 문서 추출
        sources = []
        if hasattr(response.candidates[0], 'grounding_metadata'):
            metadata = response.candidates[0].grounding_metadata
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


def should_respond_to_message(event: dict) -> bool:
    """메시지에 응답해야 하는지 판단"""
    text = event.get("text", "").lower()
    channel = event.get("channel", "")

    # 봇 자신의 메시지는 무시
    if event.get("bot_id"):
        return False

    # 특정 채널 필터링
    if config.AUTO_REPLY_CHANNELS and channel not in config.AUTO_REPLY_CHANNELS:
        return False

    # 키워드 체크
    return any(keyword in text for keyword in config.BOT_TRIGGER_KEYWORDS)


@app.post("/slack/events")
async def slack_events(request: Request):
    """Slack Events API 엔드포인트"""

    # 요청 본문 읽기
    body = await request.body()

    # 서명 검증
    if not verify_slack_signature(request, body):
        raise HTTPException(status_code=403, detail="Invalid signature")

    # JSON 파싱
    data = await request.json()

    # URL 검증 챌린지
    if data.get("type") == "url_verification":
        return {"challenge": data.get("challenge")}

    # 이벤트 처리
    if data.get("type") == "event_callback":
        event = data.get("event", {})
        event_id = data.get("event_id")

        # 중복 이벤트 방지
        if event_id in processed_events:
            return {"status": "ok"}
        processed_events.add(event_id)

        # 메시지 이벤트만 처리
        if event.get("type") == "message" and not event.get("subtype"):

            # 응답 조건 확인
            if should_respond_to_message(event):
                channel = event.get("channel")
                thread_ts = event.get("thread_ts") or event.get("ts")
                question = event.get("text")

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

                    # 답변 전송
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
    return {"status": "healthy", "service": "Day1 Proposal Bot"}


@app.get("/health")
async def health():
    """Railway 헬스체크"""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
