import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";

export async function generateMetadata(): Promise<Metadata> {
  const incoming = await headers();
  const host = incoming.get("x-forwarded-host") ?? incoming.get("host") ?? "localhost:3000";
  const protocol = incoming.get("x-forwarded-proto") ?? (host.startsWith("localhost") ? "http" : "https");
  const imageUrl = `${protocol}://${host}/og-github.png`;

  return {
    title: "AI 日报",
    description: "每天 5–8 条值得关注的 AI 要闻，附带人话术语解释。",
    openGraph: {
      title: "AI 日报",
      description: "每天 5–8 条值得关注的 AI 要闻，附带人话术语解释。",
      images: [{ url: imageUrl, width: 1733, height: 907, alt: "AI 日报" }],
    },
    twitter: {
      card: "summary_large_image",
      title: "AI 日报",
      description: "每天 5–8 条值得关注的 AI 要闻，附带人话术语解释。",
      images: [imageUrl],
    },
  };
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
