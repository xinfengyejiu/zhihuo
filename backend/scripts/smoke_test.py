"""M1 冒烟测试:注册登录 → 书架标签 → 设定库 → 卷章 → 状态机(提交/驳回/通过) → 阅读器 → 供应商配置。

用法:cd backend && python scripts/smoke_test.py
使用独立 SQLite 测试库,不影响开发数据。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp()}/smoke.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)
PASS = 0


def check(name: str, cond: bool, extra: str = ""):
    global PASS
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name} {extra}")
    if cond:
        PASS += 1
    else:
        sys.exit(1)


def main():
    with client:
        run_flow()


def run_flow():
    # ---- 注册 / 登录 ----
    r = client.post("/api/auth/register", json={"username": "author", "password": "secret66"})
    check("注册", r.status_code == 200, r.text[:120] if r.status_code != 200 else "")
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    r = client.get("/api/auth/me", headers=h)
    check("获取当前用户", r.status_code == 200 and r.json()["username"] == "author")

    r = client.post("/api/auth/login", json={"username": "author", "password": "wrong"})
    check("错误密码被拒绝", r.status_code == 401)

    # ---- 标签 ----
    r = client.post("/api/tags", json={"name": "玄幻", "type": "genre", "color": "#ff6600"}, headers=h)
    check("创建标签", r.status_code == 200)
    tag_id = r.json()["id"]

    # ---- 建书 ----
    r = client.post(
        "/api/novels",
        json={"title": "星海剑歌", "summary": "一个少年在星海时代的剑修之路。", "target_words": 2000000, "tag_ids": [tag_id]},
        headers=h,
    )
    check("创建小说", r.status_code == 200, r.text[:120] if r.status_code != 200 else "")
    novel_id = r.json()["id"]
    check("小说带标签", r.json()["tags"][0]["name"] == "玄幻")

    r = client.get("/api/novels", headers=h)
    check("书架列表", r.status_code == 200 and len(r.json()) == 1)

    # ---- 设定库 ----
    r = client.post(f"/api/novels/{novel_id}/world", json={"category": "power", "title": "剑道九境", "content": "剑士分九境…"}, headers=h)
    check("创建世界观条目", r.status_code == 200)
    r = client.post(
        f"/api/novels/{novel_id}/characters",
        json={"name": "林晚舟", "role": "protagonist", "personality": "坚韧,话少,重情", "goal": "登顶剑道"},
        headers=h,
    )
    check("创建人物卡", r.status_code == 200)
    char_id = r.json()["id"]
    r = client.post(
        f"/api/novels/{novel_id}/characters",
        json={"name": "苏轻眉", "role": "support", "personality": "机敏"},
        headers=h,
    )
    char2_id = r.json()["id"]
    r = client.post(
        f"/api/novels/{novel_id}/relationships",
        json={"from_character_id": char_id, "to_character_id": char2_id, "type": "ally", "intimacy": 60, "note": "同门"},
        headers=h,
    )
    check("创建关系", r.status_code == 200)
    r = client.post(f"/api/novels/{novel_id}/rules", json={"content": "主角不杀无辜之人", "priority": 10}, headers=h)
    check("创建规则", r.status_code == 200)
    r = client.post(f"/api/novels/{novel_id}/rules", json={"content": "单章2000-3000字,结尾留钩子", "priority": 5}, headers=h)
    rule2 = r.json()["id"]

    # ---- 卷与章节 ----
    r = client.post(f"/api/novels/{novel_id}/volumes", json={"title": "第一卷 出山"}, headers=h)
    check("创建卷", r.status_code == 200)
    volume_id = r.json()["id"]
    r = client.post(
        f"/api/novels/{novel_id}/chapters",
        json={"title": "第一章 雪夜", "volume_id": volume_id, "outline": "雪夜初遇,埋下身世伏笔", "characters_involved": [char_id]},
        headers=h,
    )
    check("创建章节", r.status_code == 200)
    chapter_id = r.json()["id"]
    r = client.post(f"/api/novels/{novel_id}/chapters", json={"title": "第二章 下山", "volume_id": volume_id}, headers=h)
    chapter2_id = r.json()["id"]

    # ---- 状态机:字数不足不能提交 ----
    r = client.post(f"/api/novels/{novel_id}/chapters/{chapter_id}/submit", headers=h)
    check("字数不足拒绝提交", r.status_code == 400)

    # ---- 保存正文 ----
    content = "雪落了整夜。" + "少年握紧了剑。他想起师父说过的话,剑是心,不是铁。" * 10
    r = client.put(f"/api/novels/{novel_id}/chapters/{chapter_id}/content", json={"content": content}, headers=h)
    check("保存正文", r.status_code == 200 and r.json()["word_count"] > 100)
    check("状态为草稿", r.json()["status"] == "draft")

    # ---- 提交审核 ----
    r = client.post(f"/api/novels/{novel_id}/chapters/{chapter_id}/submit", headers=h)
    check("提交审核", r.status_code == 200 and r.json()["status"] == "pending_review")

    # ---- 审核详情 ----
    r = client.get(f"/api/review/chapters/{chapter_id}", headers=h)
    check("审核详情含当前稿", r.status_code == 200 and r.json()["current"] is not None)
    r = client.post(f"/api/review/chapters/{chapter_id}/ai-review", json={"skill_ids": []}, headers=h)
    check("未配置AI时审查明确报错", r.status_code in (400, 502), f"got {r.status_code}")

    # ---- 阅读器:未通过章节不可读 ----
    r = client.get(f"/api/reader/novels/{novel_id}/chapters/{chapter_id}", headers=h)
    check("未审核章节不可读", r.status_code == 403)

    # ---- 驳回(必须填意见) ----
    r = client.post(f"/api/review/chapters/{chapter_id}/reject", json={}, headers=h)
    check("驳回必须填意见", r.status_code == 400)
    r = client.post(f"/api/review/chapters/{chapter_id}/reject", json={"comment": "开头节奏太慢"}, headers=h)
    check("驳回成功", r.status_code == 200 and r.json()["status"] == "rejected")

    # ---- 驳回后修改 → 自动回草稿 → 再提交 → 通过 ----
    r = client.put(f"/api/novels/{novel_id}/chapters/{chapter_id}/content", json={"content": content + "忽然,雪停了。远处传来一声剑鸣。"}, headers=h)
    check("驳回后保存回草稿", r.json()["status"] == "draft")
    client.post(f"/api/novels/{novel_id}/chapters/{chapter_id}/submit", headers=h)
    r = client.post(f"/api/review/chapters/{chapter_id}/approve", json={"comment": "可以"}, headers=h)
    check("审核通过", r.status_code == 200 and r.json()["status"] == "approved")

    # ---- 阅读器可读 ----
    r = client.get(f"/api/reader/novels/{novel_id}/catalog", headers=h)
    check("阅读器目录含已通过章节", len(r.json()["chapters"]) == 1)
    r = client.get(f"/api/reader/novels/{novel_id}/chapters/{chapter_id}", headers=h)
    check("阅读器可读正文", r.status_code == 200 and "剑鸣" in r.json()["content"])
    check("阅读进度已记录", r.json()["prev_id"] is None and r.json()["next_id"] is None)
    r = client.get(f"/api/reader/novels/{novel_id}/catalog")
    check("未登录可看目录", r.status_code == 200 and len(r.json()["chapters"]) == 1)
    r = client.get(f"/api/reader/novels/{novel_id}/chapters/{chapter_id}")
    check("未登录可看正文", r.status_code == 200 and "剑鸣" in r.json()["content"])

    # ---- 书架统计 ----
    r = client.get(f"/api/novels/{novel_id}/stats", headers=h)
    check("统计已通过字数", r.json()["by_status"].get("approved", {}).get("count", 0) == 1)

    # ---- 摘要(未配置 AI 应报错但不崩) ----
    r = client.post(f"/api/novels/{novel_id}/summary/refresh", headers=h)
    check("未配置AI时摘要报400", r.status_code == 400)

    # ---- 供应商配置 ----
    r = client.post(
        "/api/providers",
        json={"name": "GitCC", "base_url": "https://gitcc.example.com/v1", "api_key": "sk-test-1234567890abcdef"},
        headers=h,
    )
    check("创建供应商", r.status_code == 200)
    provider_id = r.json()["id"]
    check("api_key已脱敏", r.json()["api_key_masked"] == "sk-t****cdef")
    r = client.post(f"/api/providers/{provider_id}/test", headers=h)
    check("测试连通返回结构", r.status_code == 200 and "ok" in r.json())

    r = client.put(f"/api/routes/chapter", json={"provider_id": provider_id, "model_id": "gpt-test", "temperature": 0.8, "max_tokens": 4096}, headers=h)
    check("配置正文路由", r.status_code == 200)
    r = client.get("/api/routes", headers=h)
    check("路由列表7个用途", len(r.json()) == 7)
    route_chapter = next(x for x in r.json() if x["purpose"] == "chapter")
    check("路由已保存", route_chapter["provider_id"] == provider_id and route_chapter["model_id"] == "gpt-test")

    # ---- 上下文预览 ----
    r = client.get(f"/api/novels/{novel_id}/chapters/{chapter2_id}/context-preview", headers=h)
    check("上下文包含规则", "主角不杀无辜之人" in r.json()["messages"][0]["content"])
    check("上下文包含人物卡", "林晚舟" in r.json()["messages"][0]["content"])

    # ---- 用量 ----
    r = client.get("/api/usage/summary", headers=h)
    check("用量统计", r.status_code == 200)

    # ---- 个人中心 ----
    r = client.put("/api/profile", json={"nickname": "老王", "email": "a@b.com"}, headers=h)
    check("更新资料", r.json()["nickname"] == "老王")
    r = client.put("/api/profile/password", json={"old_password": "secret66", "new_password": "secret77"}, headers=h)
    check("修改密码", r.status_code == 200)
    r = client.post("/api/auth/login", json={"username": "author", "password": "secret77"})
    check("新密码可登录", r.status_code == 200)

    # ---- 规则启停 ----
    r = client.put(f"/api/novels/{novel_id}/rules/{rule2}", json={"content": "单章2000-3000字,结尾留钩子", "priority": 5, "enabled": False}, headers=h)
    check("停用规则", r.json()["enabled"] is False)

    # ---- 发布中心 ----
    r = client.get("/api/publish/platforms")
    check("平台常量列表", r.status_code == 200 and any(p["key"] == "fanqie" for p in r.json()))

    r = client.post(
        "/api/novels",
        json={"title": "多平台之书", "platforms": ["fanqie", "qimao"], "tag_ids": []},
        headers=h,
    )
    check("建书时声明平台", r.status_code == 200 and {p["platform"] for p in r.json()["platforms"]} == {"fanqie", "qimao"})
    novel2_id = r.json()["id"]

    # 编辑书的平台:去掉七猫,加起点
    r = client.put(f"/api/novels/{novel2_id}", json={"platforms": ["fanqie", "qidian"]}, headers=h)
    check("编辑书平台", {p["platform"] for p in r.json()["platforms"]} == {"fanqie", "qidian"})

    # 非法平台被过滤
    r = client.put(f"/api/novels/{novel2_id}", json={"platforms": ["fanqie", "bad_platform"]}, headers=h)
    check("非法平台被过滤", {p["platform"] for p in r.json()["platforms"]} == {"fanqie"})

    # 用已审核通过的章节创建发布任务(番茄 未来排期,七猫 已到期排期)
    r = client.post(f"/api/novels/{novel_id}/chapters", json={"title": "发布测试章"}, headers=h)
    pub_chapter_id = r.json()["id"]
    client.put(f"/api/novels/{novel_id}/chapters/{pub_chapter_id}/content", json={"content": "发布测试正文" * 30}, headers=h)
    client.post(f"/api/novels/{novel_id}/chapters/{pub_chapter_id}/submit", headers=h)
    client.post(f"/api/review/chapters/{pub_chapter_id}/approve", json={}, headers=h)

    future = "2099-01-01T12:00:00Z"
    past = "2020-01-01T00:00:00Z"
    r = client.post(
        "/api/publish/tasks",
        json={"chapter_ids": [pub_chapter_id], "platforms": ["fanqie"], "scheduled_at": future},
        headers=h,
    )
    r2 = client.post(
        "/api/publish/tasks",
        json={"chapter_ids": [pub_chapter_id], "platforms": ["qimao"], "scheduled_at": past},
        headers=h,
    )
    all_tasks = (r.json() if r.status_code == 200 else []) + (r2.json() if r2.status_code == 200 else [])
    check("创建发布任务", len(all_tasks) == 2, str(r.status_code) + r.text[:120])
    task_fanqie = next(t for t in all_tasks if t["platform"] == "fanqie")
    task_qimao = next(t for t in all_tasks if t["platform"] == "qimao")
    check("任务进入排期", task_fanqie["status"] == "scheduled" and task_qimao["status"] == "scheduled")

    r = client.get(f"/api/novels/{novel_id}/chapters/{pub_chapter_id}", headers=h)
    check("任务创建后章节变为已排期", r.json()["status"] == "scheduled")

    # 未审核章节不能发布
    r = client.post(f"/api/novels/{novel_id}/chapters", json={"title": "未审章节"}, headers=h)
    draft_id = r.json()["id"]
    r = client.post("/api/publish/tasks", json={"chapter_ids": [draft_id], "platforms": ["fanqie"]}, headers=h)
    check("未审核章节拒绝发布", r.status_code == 400)

    # 同平台重复任务不重复创建
    r = client.post("/api/publish/tasks", json={"chapter_ids": [pub_chapter_id], "platforms": ["fanqie"]}, headers=h)
    check("重复任务不重复建", len(r.json()) == 1 and r.json()[0]["id"] == task_fanqie["id"])

    # 番茄标记成功 → 章节仍有一个未完成平台任务(scheduled)保持排期;七猫到期 → need_manual
    r = client.post(f"/api/publish/tasks/{task_fanqie['id']}/success", json={"platform_chapter_id": "FQ-123"}, headers=h)
    check("标记发布成功", r.json()["status"] == "success" and r.json()["platform_chapter_id"] == "FQ-123")
    r = client.get(f"/api/novels/{novel_id}/chapters/{pub_chapter_id}", headers=h)
    check("部分平台未完成时保持排期", r.json()["status"] == "scheduled")

    r = client.get(f"/api/publish/tasks?novel_id={novel_id}", headers=h)
    qimao_task = next(t for t in r.json() if t["id"] == task_qimao["id"])
    check("到期定时任务转人工待办", qimao_task["status"] == "need_manual")

    # 七猫也标记成功 → 章节变为已发布,平台进度更新
    client.post(f"/api/publish/tasks/{task_qimao['id']}/success", json={}, headers=h)
    r = client.get(f"/api/novels/{novel_id}/chapters/{pub_chapter_id}", headers=h)
    check("全部平台完成后章节已发布", r.json()["status"] == "published")
    r = client.get(f"/api/novels/{novel_id}", headers=h)
    fanqie_link = next(p for p in r.json()["platforms"] if p["platform"] == "fanqie")
    check("平台发布进度已更新", fanqie_link["published_chapter_no"] >= 1)

    # 平台账号状态
    r = client.put("/api/publish/accounts/fanqie", json={"status": "logged_in", "note": "测试号"}, headers=h)
    check("标记平台账号已登录", r.json()["status"] == "logged_in")
    r = client.get("/api/publish/accounts", headers=h)
    check("账号列表覆盖全部平台", len(r.json()) == 6)

    # 重试失败任务
    r = client.post(f"/api/publish/tasks/{task_fanqie['id']}/fail", json={"error": "验证码"}, headers=h)
    check("标记失败", r.json()["status"] == "failed")
    r = client.post(f"/api/publish/tasks/{task_fanqie['id']}/retry", headers=h)
    check("重试回队列", r.json()["status"] == "queued")

    # ---- M2 知识库 ----
    r = client.post("/api/kb", json={"name": "我的设定资料", "description": "力量体系参考"}, headers=h)
    check("创建知识库", r.status_code == 200)
    kb_id = r.json()["id"]
    files = {"file": ("资料.txt", "剑道九境:入门、凝气、剑心、剑罡。每境分三重。".encode("utf-8"), "text/plain")}
    r = client.post(f"/api/kb/{kb_id}/upload", files=files, headers=h)
    check("上传TXT并分块", r.status_code == 200 and r.json()["chunk_count"] >= 1, r.text[:150] if r.status_code != 200 else "")
    doc_id = r.json()["id"]
    r = client.get(f"/api/kb/{kb_id}/documents", headers=h)
    check("文档列表", len(r.json()) == 1)
    r = client.post(f"/api/kb/{kb_id}/search", json={"query": "剑道境界划分"}, headers=h)
    check("未配Embedding时检索给明确提示", r.status_code == 400)
    # 挂载到书
    r = client.put(f"/api/novels/{novel_id}/kbs", json={"kb_ids": [kb_id]}, headers=h)
    check("知识库挂载到书", r.json()["kb_ids"] == [kb_id])
    r = client.get(f"/api/novels/{novel_id}/kbs", headers=h)
    check("读取挂载", r.json()["kb_ids"] == [kb_id])

    # ---- M2 Skills ----
    r = client.post("/api/skills/seed-presets", headers=h)
    check("导入预置技能", r.json()["created"] >= 9)
    r = client.get("/api/skills", headers=h)
    check("技能列表含预置", any(s["builtin"] for s in r.json()) and len(r.json()) >= 5)
    skill_chapter = next(s for s in r.json() if s["stage"] == "chapter")
    r = client.post(
        "/api/skills/import-markdown",
        json={"name": "我的自定义技能", "stage": "chapter", "markdown": "---\nname: 我的自定义技能\n---\n正文要短句为主。"},
        headers=h,
    )
    check("SKILL.md 导入(frontmatter 名字)", r.json()["name"] == "我的自定义技能")
    custom_skill_id = r.json()["id"]
    r = client.put(f"/api/novels/{novel_id}/skills", json={"stage": "chapter", "skill_id": skill_chapter["id"]}, headers=h)
    check("挂载技能到书", r.status_code == 200)
    r = client.get(f"/api/novels/{novel_id}/skills", headers=h)
    check("读取挂载", r.json().get("chapter") == skill_chapter["id"])

    # ---- M3 伏笔台账 ----
    r = client.post(
        f"/api/novels/{novel_id}/foreshadows",
        json={"description": "主角父亲的剑穗来历", "plant_chapter_id": pub_chapter_id, "status": "planted"},
        headers=h,
    )
    check("创建伏笔", r.status_code == 200)
    foreshadow_id = r.json()["id"]
    r = client.put(
        f"/api/novels/{novel_id}/foreshadows/{foreshadow_id}",
        json={"description": "主角父亲的剑穗来历", "plant_chapter_id": pub_chapter_id, "status": "recalled"},
        headers=h,
    )
    check("更新伏笔状态", r.json()["status"] == "recalled")
    r = client.get(f"/api/novels/{novel_id}/foreshadows", headers=h)
    check("伏笔列表", len(r.json()) == 1)

    # ---- M3 复盘(未配置 AI 应 400) ----
    r = client.post(f"/api/novels/{novel_id}/chapters/{pub_chapter_id}/analyze", headers=h)
    check("未配置AI时复盘报400", r.status_code == 400)

    # ---- AI 批量导入(未配置 AI 报 400;配置了但不可达报 502,均不静默) ----
    for kind in ("characters", "relationships", "foreshadows", "world"):
        r = client.post(
            f"/api/novels/{novel_id}/import-{kind}",
            json={"markdown": "### 林晚舟\n- 定位:主角\n- 性格:坚韧、话少\n\n### 苏轻眉\n- 定位:同门师姐"},
            headers=h,
        )
        check(f"AI导入-{kind}失败时明确报错", r.status_code in (400, 502), f"got {r.status_code}")
    r = client.post(f"/api/novels/{novel_id}/import-characters", json={"markdown": "太短"}, headers=h)
    check("AI导入文本过短报400", r.status_code == 400)

    # ---- 总大纲文档(与卷/章节独立;章纲已填时正文生成不灌整本大纲) ----
    r = client.post(
        f"/api/novels/{novel_id}/outline-docs",
        json={"title": "全书大纲", "content": "第一卷:主角觉醒剑心;第二卷:宗门大比夺魁;第三卷:发现身世之谜。"},
        headers=h,
    )
    check("创建总大纲文档", r.status_code == 200)
    outline_doc_id = r.json()["id"]
    r = client.get(f"/api/novels/{novel_id}/chapters/{chapter2_id}/context-preview", headers=h)
    preview = r.json()["messages"][1]["content"]
    check("生成上下文含邻近章纲窗口", "邻近章纲窗口" in preview or "本章目标" in preview)
    check("生成上下文含策略字段", r.json().get("strategy", "").startswith("rolling_summary"))
    r = client.get(f"/api/novels/{novel_id}/outline-docs", headers=h)
    check("大纲文档列表含字数", len(r.json()) == 1 and r.json()[0]["word_count"] > 0)
    r = client.put(
        f"/api/novels/{novel_id}/outline-docs/{outline_doc_id}",
        json={"title": "全书大纲v2", "content": "更新后的走向"},
        headers=h,
    )
    check("编辑大纲文档", r.json()["title"] == "全书大纲v2")
    r = client.delete(f"/api/novels/{novel_id}/outline-docs/{outline_doc_id}", headers=h)
    check("删除大纲文档", r.status_code == 200)

    # ---- 一书一对话 ----
    r = client.get(f"/api/chat/novels/{novel_id}/session", headers=h)
    check("获取/创建书内对话", r.status_code == 200 and r.json().get("novel_id") == novel_id)
    novel_chat_id = r.json()["id"]
    r2 = client.get(f"/api/chat/novels/{novel_id}/session", headers=h)
    check("书内对话幂等同一 id", r2.json()["id"] == novel_chat_id)

    # ---- 首页自由对话 ----
    r = client.post("/api/chat/sessions", json={}, headers=h)
    check("创建对话会话", r.status_code == 200)
    chat_sid = r.json()["id"]
    r = client.get(f"/api/chat/sessions/{chat_sid}/messages", headers=h)
    check("新会话无消息", r.json() == [])
    r = client.post(f"/api/chat/sessions/{chat_sid}/chat", json={"message": "帮我构思一个修仙文开局"}, headers=h)
    check("对话接口流式返回", r.status_code == 200)
    check("无可用AI时对话返回错误事件", '"error"' in r.text)
    r = client.get(f"/api/chat/sessions/{chat_sid}/messages", headers=h)
    check("用户消息已落库", any(m["role"] == "user" and "修仙文" in m["content"] for m in r.json()))
    r = client.get("/api/chat/sessions", headers=h)
    check("首条消息自动作为会话标题", any(s["id"] == chat_sid and s["title"].startswith("帮我构思") for s in r.json()))
    r = client.put(f"/api/chat/sessions/{chat_sid}", json={"title": "我的选题讨论"}, headers=h)
    check("重命名会话", r.json()["title"] == "我的选题讨论")
    r = client.post(f"/api/chat/sessions/{chat_sid}/apply-to-novel", headers=h)
    check("应用到书架:AI不可达明确报错", r.status_code in (400, 502), f"got {r.status_code}")
    r = client.delete(f"/api/chat/sessions/{chat_sid}", headers=h)
    check("删除会话", r.status_code == 200)

    # ---- M4 导出 ----
    r = client.get(f"/api/novels/{novel_id}/export?format=txt", headers=h)
    check("导出TXT", r.status_code == 200 and "雪落了整夜" in r.text)
    r = client.get(f"/api/novels/{novel_id}/export?format=epub", headers=h)
    check("导出EPUB", r.status_code == 200 and r.content[:2] == b"PK")

    r = client.post(
        f"/api/novels/{novel_id}/apply-outline",
        json={"text": "卷:第二卷 试炼之路\n第一章 试炼开始\n第二章 深山|遭遇狼群|林晚舟\n\n卷:第三卷 归来\n## 第一章 归宗", "new_volume_title": "第一卷 起点"},
        headers=h,
    )
    res = r.json()
    check(
        "批量建卷+章节(卷行声明)",
        r.status_code == 200 and res["created"] == 3 and res["volumes_created"] == 2,
        str(res)[:200],
    )
    catalog = {
        "volumes": [
            {
                "title": "第四卷 终局",
                "outline": "收束",
                "chapters": [{"title": "决战", "outline": "终局之战", "characters": "林晚舟"}],
            }
        ]
    }
    import json as _json

    wrapped = "```json\n" + _json.dumps(catalog, ensure_ascii=False) + "\n```"
    r = client.post(f"/api/novels/{novel_id}/apply-outline", json={"text": wrapped, "dry_run": True}, headers=h)
    check("JSON章纲预览", r.status_code == 200 and r.json()["created"] == 1 and r.json()["volumes"][0]["chapters"][0]["title"] == "决战", str(r.json())[:200])
    r = client.post(f"/api/novels/{novel_id}/apply-outline", json={"text": wrapped}, headers=h)
    check("JSON章纲套用", r.status_code == 200 and r.json()["created"] == 1 and r.json()["volumes_created"] == 1, str(r.json())[:200])
    r = client.post(f"/api/novels/{novel_id}/apply-outline", json={"text": wrapped}, headers=h)
    check("JSON章纲不重复创建", r.status_code == 200 and r.json()["created"] == 0, str(r.json())[:200])

    # ---- M4 团队协作 ----
    client.post("/api/auth/register", json={"username": "reviewer1", "password": "secret66"})
    r = client.post(f"/api/novels/{novel_id}/collaborators", json={"username": "reviewer1", "role": "reviewer"}, headers=h)
    check("邀请协作者", r.status_code == 200 and r.json()["role"] == "reviewer")
    collab_id = r.json()["id"]
    rt = client.post("/api/auth/login", json={"username": "reviewer1", "password": "secret66"}).json()["access_token"]
    rh = {"Authorization": f"Bearer {rt}"}
    r = client.get("/api/novels", headers=rh)
    check("协作者书架可见共享书", any(n["id"] == novel_id for n in r.json()))
    r = client.get(f"/api/novels/{novel_id}", headers=rh)
    check("协作者角色字段", r.json()["my_role"] == "reviewer")
    r = client.get(f"/api/novels/{novel_id}/chapters/{chapter_id}/content", headers=rh)
    check("协作者可读章节", r.status_code == 200)
    r = client.delete(f"/api/novels/{novel_id}", headers=rh)
    check("协作者不能删书", r.status_code == 403)
    r = client.post(f"/api/review/chapters/{pub_chapter_id}/approve", json={}, headers=rh)
    check("非待审章节通过被拒", r.status_code == 400)
    r = client.get(f"/api/novels/{novel_id}/collaborators", headers=h)
    check("协作者列表", len(r.json()) == 1)
    client.delete(f"/api/novels/{novel_id}/collaborators/{collab_id}", headers=h)

    print(f"\n全部 {PASS} 项检查通过 ✔")


if __name__ == "__main__":
    main()
