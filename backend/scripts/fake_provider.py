"""假 OpenAI 流式服务器:验证流式链路每一环是否增量输出。监听 127.0.0.1:9009。"""

import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()


@app.post("/v1/chat/completions")
async def completions(request: Request):
    async def gen():
        # 先模拟思考型模型的 reasoning_content 阶段
        for i in range(3):
            yield (
                "data: "
                + json.dumps(
                    {"choices": [{"delta": {"reasoning_content": f"思考第{i}步:分析需求…"}}]},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            await asyncio.sleep(0.5)
        for i in range(6):
            yield f"data: {json.dumps({'choices': [{'delta': {'content': f'第{i}块内容。'}}]}, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.4)
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=9009, log_level="warning")

