"""Fake HTTP provider for explicitly invoked tests only; not shipped in the image."""
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()


@app.post("/v1/chat/completions")
async def completion(body: dict) -> JSONResponse:
    question = body["messages"][-1]["content"]
    if "provider-error" in question:
        return JSONResponse({"error": "test quota"}, status_code=429)
    text = "## План\n1. **Уточнить цель.** Согласовать результат.\n\n### Следующий шаг\nОбсудить ограничения."
    if "xss-test" in question:
        text += '\n<script>window.pwned=true</script>\n<img src=x onerror="window.pwned=true">'
    return JSONResponse({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})
