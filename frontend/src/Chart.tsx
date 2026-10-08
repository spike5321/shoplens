import { useEffect, useRef } from "react";
import { init, use } from "echarts/core";
import { BarChart, LineChart } from "echarts/charts";
import { GridComponent, TooltipComponent } from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { ChartData } from "./api";

use([BarChart, LineChart, GridComponent, TooltipComponent, CanvasRenderer]);

export default function Chart({ data }: { data: ChartData }) {
  const element = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!element.current) return;
    const chart = init(element.current);
    const divisor = data.unit.startsWith("fen") ? 100 : 1;
    chart.setOption({
      color: ["#6658d9"],
      animation: false,
      grid: { left: 68, right: 24, top: 24, bottom: 66 },
      tooltip: {
        trigger: "axis",
        valueFormatter: (v: number | null) =>
          v === null
            ? "不可计算"
            : v.toLocaleString("zh-CN", { maximumFractionDigits: 2 }),
      },
      xAxis: {
        type: "category",
        data: data.labels,
        axisTick: { show: false },
        axisLine: { lineStyle: { color: "#dddbe5" } },
        axisLabel: {
          color: "#777283",
          rotate: data.labels.length > 8 ? 45 : 0,
          hideOverlap: true,
        },
      },
      yAxis: {
        type: "value",
        axisLabel: { color: "#777283" },
        splitLine: { lineStyle: { color: "#efedf3", type: "dashed" } },
      },
      series: [
        {
          type: data.type,
          data: data.values.map((v) =>
            v === null ? null : Number(v) / divisor,
          ),
          barMaxWidth: 56,
          itemStyle: { borderRadius: [5, 5, 0, 0] },
          showSymbol: data.labels.length < 16,
          lineStyle: { width: 3 },
          areaStyle: data.type === "line" ? { opacity: 0.06 } : undefined,
        },
      ],
    });
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(element.current);
    return () => {
      observer.disconnect();
      chart.dispose();
    };
  }, [data]);
  return (
    <div
      ref={element}
      className="chart"
      role="img"
      aria-label={`${data.type === "bar" ? "条形图" : "折线图"}，数值详见下方表格`}
    />
  );
}
