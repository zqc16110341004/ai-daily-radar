import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "AI 日报",
  description: "每天 5–8 条值得关注的 AI 要闻，附带人话术语解释。",
};

export default function Home() {
  return (
    <main className="daily-shell">
      <iframe
        className="daily-frame"
        src="/ai-daily.html"
        title="AI 日报"
      />
    </main>
  );
}
