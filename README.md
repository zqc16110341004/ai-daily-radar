# AI 日报

一个每日更新的中文 AI 新闻与 GitHub 开源项目雷达站点。页面内容由上级目录中的日报流程生成，并以静态 HTML 嵌入这个 vinext 站点。

在线阅读：<https://ai-daily-radar.jcarter969.chatgpt.site>

## 内容范围

- 每期 5–8 条 AI 要闻，侧重原始来源与实质影响。
- 每期 3–4 个 GitHub 项目，说明用途、差异、适用人群、入选原因与风险。
- 术语会标注为知识库已有概念或当期临时解释。

## 本地运行

需要 Node.js 22.13 或更高版本：

```bash
npm install
npm run dev
```

## 验证

```bash
npm test
```

该命令会构建站点，并检查生成的日报页面包含必要的新闻和 GitHub 雷达结构。

## 更新日报

此仓库只包含站点代码和已生成页面。完整编辑流程位于父项目：先生成 `wiki/outputs/ai-daily-YYYY-MM-DD.md`，再运行：

```bash
python3 scripts/ai_daily/render.py --latest
```

它会更新本项目的 `public/ai-daily.html`；验证通过后即可提交并发布。
