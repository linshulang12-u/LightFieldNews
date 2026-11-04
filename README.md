# LightFieldNews

光场调控（结构光）领域的社区周报网站，带一个独立的 **时空光场（Spatiotemporal Light Fields）** 栏目。形式与代码框架沿用 [VibraNews](https://github.com/ForestBin-Raman/VibraNews)（ForestBin-Raman），和姊妹站 [FemtoNews](https://linshulang12-u.github.io/FemtoNews/) 共用同一套抓取与选稿流程：**每天自动抓取并发布新论文**，按周归为一期（每周一开新一期），编辑随时可以修改、删除、选特稿。

- 网站：纯静态页面（`index.html`），托管在 GitHub Pages，读取 `data/issues/*.json`
- 抓取：`scraper/news_scraper.py`，数据源为 OpenAlex、Crossref、Europe PMC、arXiv（免费接口，不需要 API key）
- 自动化：GitHub Actions，每天新加坡时间 06:33 运行（`daily-update.yml`），抓过去 3 天的新论文，直接发布
- 第 1 期（2026-11-02 周）已预置 13 篇 Feature Articles（时空栏目 6 篇），摘要和 “Why it matters” 由编辑撰写；其余论文由第一次抓取补齐

---

## 时空光场栏目

- 入口：菜单 **Spatiotemporal 时空光场**，或直接打开 https://linshulang12-u.github.io/LightFieldNews/#spacetime （可以单独分享这个链接）
- 栏目页只显示时空光场论文：自己的 Feature 列表（置顶的是 **Spatiotemporal Pick of the Week**，`"st_feature": true`）、自己的最新论文和分类
- 首页：Feature Articles 只放非时空的特稿，时空特稿集中在首页的栏目预告框里；论文列表里时空论文带 `ST` 标记
- 分类判定：论文只要打上 “Spatiotemporal Light Fields” 组的任一标签，就进入栏目

## 收录方向（分类体系）

侧栏 “Topics” 按五组分类，点 “中文” 可切换为中文名称。规则全部在 `scraper/config.yaml` 的 `categories`。

| 组 | 分类 |
|---|---|
| 时空光场 Spatiotemporal | 时空波包 · 时空光学涡旋 (STOV) · 环形与拓扑脉冲 · 光弹与 X 波 · 时空聚焦 (SSTF) · 飞行焦点与可编程脉冲 · 多模与时空锁模 · 时变介质与时间界面 · 时空场测量 |
| 光场调控方向 Directions | 涡旋光与轨道角动量 · 矢量光与偏振调控 · 无衍射与自加速光束 · 拓扑光场（斯格明子、光结）· 超表面与平面光学 · SLM、DMD 与全息 · 波前整形与散射介质 · 自适应光学与像差 · 光镊与光学操控 · 紧聚焦与焦场 · 相干性与部分相干光场 · 模式复用与光纤 · 非线性与量子结构光 · 结构光激光器与光源 |
| 方法 Methods | 逆向设计与 AI · 理论与仿真 · 表征与测量 · 集成与片上 |
| 应用 Applications | 光通信 · 显微与成像 · 激光加工 · 光计算 · 量子信息 · 传感与计量 · 强场与超快 · 湍流与自由空间链路 · 显示、加密与 AR |
| 其他 | 新闻与亮点 · 综述 · 招聘与招生 · 学位论文 |

每篇论文最多 7 个标签（时空 2、方向 2、方法 1、应用 1、其他 1，见 `category_limits`）。没有可用配图时，网站按第一个命中的分类画一个光场示意小图（涡旋相位环、矢量偏振、Bessel 环、STOV 时空涡旋、环形脉冲……，见 `glyphs`）。

## 网址与运行方式

- **网站**：https://linshulang12-u.github.io/LightFieldNews/
- **单文件版**：https://linshulang12-u.github.io/LightFieldNews/LightFieldNews.html 把全部数据嵌在一个 html 里，下载后双击即可离线浏览；缩略图从网站加载。
- **Windows 本地副本**：双击 `setup_windows.bat`，每天 08:27 自动下载最新的 `LightFieldNews.html`（不需要 Python）。
- **本机抓取（可选）**：`setup_local_crawl.bat`，和 GitHub 方式二选一。

## 日常流程

1. **每天 06:33（新加坡时间）**：自动抓取过去 3 天的新论文（OpenAlex 回看 30 天），加入当周这一期，直接发布。已出现过或被删除过的论文不会重复出现。
2. **随时修改**：在 GitHub 网页上编辑当期的 `data/issues/<日期>.json`，保存后网站自动更新：
   - 删掉不相关的文章，并把它的 `id` 加进 `"removed"`
   - **Feature of the Week**：`"feature": true`；**Spatiotemporal Pick of the Week**：`"st_feature": true`；**Feature Articles**：`"pick": true`；在 `"why"` 写一句为什么重要
3. **投稿 / 手动加文章**：把 DOI 一行一个写进 `data/inbox.txt` 并保存，会**立即**触发一次抓取（不受关键词过滤），顺便给手动加入的文章补缩略图，处理完自动从 inbox 删除。
4. **选稿助手**：`python scraper/curate.py apply <文件>`，JSON 里可写 `picks`、`feature`、`st_feature`、`unpick`、`remove`。每期最多 15 篇特稿，超出时替换入选最早的，两个 Pick of the Week 不会被替换。

## 抓取范围与去杂

全部在 `scraper/config.yaml`：

- **收录**（`include` / `strong_include`）：结构光、涡旋 / OAM、矢量光、Bessel / Airy、光学斯格明子 / hopfion、波前整形、SLM 全息、超表面光场调控、STOV、时空波包、环形脉冲、光弹、时空聚焦、时变介质、模式复用、部分相干光、衍射神经网络等。专门术语在摘要里出现一次即收录。
- **排除**（`exclude` / `exclude_title`）：声学涡旋、磁斯格明子、电子 / 中子 / 物质波涡旋、流体涡、无线 / 天线 / 毫米波、天体物理、泛泛的 “spatiotemporal analysis / forecasting”，以及光场相机（plenoptic light-field imaging，和光场调控不是一回事）。
- **期刊质量**（`venues`）：主流出版社和 arXiv 直接收；Research Square、CyberLeninka 等排除；其他期刊 OpenAlex h-index ≥ 40 才收。

## 本地运行

```
pip install -r scraper/requirements.txt pytest
python -m pytest -q scraper/tests                 # 离线自测
python scraper/news_scraper.py --daily            # 每日模式
python scraper/build_standalone.py                # 重建 LightFieldNews.html
python -m http.server 8000                        # 浏览 http://localhost:8000
```

## 致谢

页面布局、抓取与选稿流程改编自 ForestBin-Raman 的 [VibraNews](https://github.com/ForestBin-Raman/VibraNews)。公开发布前建议和原作者打个招呼，并保留这条致谢。
