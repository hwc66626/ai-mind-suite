/* 图表初始化：Mermaid 流程图（本报告无量化数据图表，故不引入 ECharts） */
(function () {
  if (typeof mermaid === "undefined") return;
  mermaid.initialize({
    startOnLoad: true,
    theme: "neutral",
    securityLevel: "loose",
    fontFamily: '"WorkSans", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
    flowchart: { curve: "basis", useMaxWidth: true }
  });
})();
