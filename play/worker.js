/* ==========================================================================
   引擎线程。整个 Python 都在这里，主线程一行 Python 都不跑。

   **为什么必须是 Worker。** 玩家「亲自看一场」时走的是完整引擎逐秒模拟，
   实测本地 CPython 要 20–48 秒，Pyodide 还要再乘 2.3。这段时间里 Python
   是一个不会让出控制权的死循环——放在主线程上，浏览器会直接判定页面无响应，
   连"正在计算"这四个字都画不出来。

   顺带一个好处：主线程和引擎之间只剩 postMessage 一个口子，和本地版
   只剩 HTTP 一个口子是同构的。要换 Unity 前端，换的还是这一层。
   ========================================================================== */

let PY = null, bridge = null, savePath = null;

const post = (m) => self.postMessage(m);
const progress = (pct, msg) => post({ type: "progress", pct, msg });

async function boot(cfg) {
  progress(8, "正在拉 Python 运行时…");
  // 依次试：打包时配的地址 → 官方 CDN。
  // 这个回退是有来历的：一次带 --local-pyodide 的打包被直接发上了 Pages，
  // 页面指向一个根本没提交的本地运行时，玩家看到的只有一句
  // 「Load failed」——既不知道是哪个文件没了，也没有任何补救。
  const cands = [cfg.pyodideUrl];
  if (cfg.fallbackUrl && cfg.fallbackUrl !== cfg.pyodideUrl) cands.push(cfg.fallbackUrl);
  let lastErr = null;
  for (const url of cands) {
    try {
      self.importScripts(url);
      PY = await self.loadPyodide({ indexURL: url.replace(/\/[^/]*$/, "/") });
      lastErr = null;
      break;
    } catch (e) {
      lastErr = new Error("运行时加载失败：" + url + "（" + (e && e.message || e) + "）");
      PY = null;
    }
  }
  if (!PY) throw lastErr;

  // sqlite3 在 Pyodide 里是从标准库拆出去的独立包，game/records.py 在
  // import 期就要它——缺了整个引擎起不来。
  progress(32, "正在拉 sqlite3…");
  await PY.loadPackage("sqlite3");

  progress(50, "正在取引擎源码…");
  const [zip, idx] = await Promise.all([
    fetch(cfg.engineUrl).then(r => r.arrayBuffer()),
    fetch(cfg.artIndexUrl).then(r => r.ok ? r.text() : "[]").catch(() => "[]"),
  ]);
  PY.FS.writeFile("/engine.zip", new Uint8Array(zip));

  progress(72, "正在解包…");
  await PY.runPythonAsync(`
import sys, zipfile
zipfile.ZipFile("/engine.zip").extractall("/proj")
sys.path.insert(0, "/proj/source")
`);
  bridge = PY.pyimport("web.bridge");
  bridge.setup("art/", idx, ".webp");
  savePath = bridge.save_path();

  // 上一局的存档躺在主线程的 localStorage 里，塞回虚拟文件系统，
  // /api/hassave 才看得见它。
  if (cfg.save) {
    try { PY.FS.writeFile(savePath, cfg.save); }
    catch (e) { console.warn("存档写入失败", e); }
  }
  progress(100, "就绪");
}

self.onmessage = async (e) => {
  const m = e.data;
  try {
    if (m.type === "boot") {
      await boot(m);
      post({ id: m.id, ok: true });
      return;
    }
    if (m.type === "fingerprint") {
      // 对账：同一颗种子跑完一个赛季，把最终状态压成指纹。
      // 浏览器里的这一串必须和 `python3 source/verify_web.py` 一模一样，
      // 否则就说明偷偷有了两套引擎——这个项目从第一天起就在防这件事。
      const s = await PY.runPythonAsync(
        "import verify_web, json; json.dumps(verify_web.run(), ensure_ascii=False)");
      post({ id: m.id, ok: true, data: s });
      return;
    }
    const out = JSON.parse(bridge.handle(m.method, m.path, m.body || ""));
    // 存档落盘之后要把内容捎回主线程写 localStorage——Worker 自己
    // 碰不到 localStorage。
    if (m.path === "/api/save" && out.status === 200) {
      try { out.save = PY.FS.readFile(savePath, { encoding: "utf8" }); }
      catch (err) { console.warn("存档读取失败", err); }
    }
    post({ id: m.id, ok: true, ...out });
  } catch (err) {
    post({ id: m.id, ok: false, error: String(err && err.message || err) });
  }
};
