"""모델을 만드는 곳 한 군데.

제공자를 `config.json` 의 `model.provider` 로 고른다. 바꿔 끼울 수 있게 둔 이유는
하나다 — REPORT 에 적힌 측정 숫자가 **어느 모델로 잰 것인지** 구별되어야 하기 때문이다.
코드에서 제공자를 지워 버리면 예전 숫자가 무효인지 유효한지 알 수 없게 된다.

함정 하나: **Claude 5 세대는 `temperature` 를 거부한다(400).**
Opus 5 · Sonnet 5 · Fable 5 에서 샘플링 파라미터가 제거됐다. OpenAI 코드에서 옮겨 올 때
`temperature=0` 을 그대로 들고 오면 전부 실패한다. 여기서 제공자별로 갈라 막는다.
"""
import json
import os

CFG = json.load(open("config.json", encoding="utf-8"))
M = CFG["model"]
PROVIDER = M.get("provider", "openai")


def model_id(role="chat"):
    """role 은 'chat'(추출·답변) 또는 'judge'(채점). 심판은 생성과 다른 모델을 쓴다."""
    return M[PROVIDER][role]


def chat_model(role="chat", **kw):
    if PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # temperature 를 넘기지 않는다. langchain-anthropic 의 기본값이 None 이라
        # 지정하지 않으면 요청에 아예 실리지 않는다. 결정성은 포기하는 대신
        # 추출 결과를 output/cache/ 에 캐시해 재현성을 지킨다.
        return ChatAnthropic(model=model_id(role),
                             timeout=M["timeout"], max_retries=M["retries"],
                             max_tokens=8192, **kw)

    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=model_id(role), temperature=0,
                      timeout=M["timeout"], max_retries=M["retries"], **kw)


def require_key():
    """키가 없으면 무엇을 해야 하는지 알려 주고 멈춘다.

    이게 없으면 LangChain 안쪽에서 추상적인 인증 오류가 나서, 처음 돌리는 사람이
    무엇을 해야 할지 알 수 없다.
    """
    need = "ANTHROPIC_API_KEY" if PROVIDER == "anthropic" else "OPENAI_API_KEY"
    if os.getenv(need):
        return
    raise SystemExit(
        "\n%s 가 없습니다.\n"
        "  1) .env 파일에 %s=... 한 줄을 넣으세요\n"
        "  2) 키 발급: %s\n"
        "  (다른 제공자를 쓰려면 config.json 의 model.provider 를 바꾸세요. "
        "지금은 '%s' 입니다)\n"
        % (need, need,
           "https://console.anthropic.com" if PROVIDER == "anthropic"
           else "https://platform.openai.com/api-keys",
           PROVIDER))
