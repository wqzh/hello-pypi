
# 1.  压缩失败: 未找到 API key：请在 options.api_key 传入，或设置 OPENAI_API_KEY 环境变量

```text
调用路径（从上到下）：

**1. `compaction.py` → `generate_summary()`**（第 183 行）

``python
result = await complete_simple(model, ctx, opts)
``
→ 调用 `complete_simple`

---

**2. `stream.py` → `complete_simple()`**（第 48 行）

``python
def complete_simple(...):
    event_stream = stream_simple(model, context, options)
    return await event_stream.result()
``
→ 调用 `stream_simple`

---

**3. `stream.py` → `stream_simple()`**（第 28 行）

``python
def stream_simple(model, context, options):
    impl = get_api_provider(model.api)
    return impl.stream_simple(model, context, options)
``
→ `model.api` 是 `"openai"`，所以 `impl` = `openai_provider` 实例，调用它的 `stream_simple`

---

**4. `openai_provider.py` → `stream_simple()`**（第 371 行附近）

``python
api_key = (options.api_key if options else None) or _resolve_api_key()
``
→ 先看 `options.api_key`，没有就调 `_resolve_api_key()`

---

**5. `openai_provider.py` → `_resolve_api_key()`**（第 672 行）

``python
def _resolve_api_key() -> str:
    import os
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError(
            "未找到 API key：请在 options.api_key 传入，或设置 OPENAI_API_KEY 环境变量"
        )
    return key
``
→ **这里读环境变量** `OPENAI_API_KEY`，找不到就抛错。

---

**总结：** `generate_summary` 没传 `api_key` → provider 只能读环境变量 → 你环境变量没设 → 报错。



Done. `generate_summary` 的 `**options` 会透传到 `SimpleStreamOptions`，provider 能读到 `options.api_key`。

2. **或者设置环境变量**（偷懒版）：

``bash
set OPENAI_API_KEY=你的key
``

两种效果一样，推荐方案 1，不依赖环境变量。
```