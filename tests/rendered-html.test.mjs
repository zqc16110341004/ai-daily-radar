import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("ships the AI daily shell and generated page", async () => {
  const [page, layout, daily] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/layout.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/ai-daily.html", import.meta.url), "utf8"),
  ]);

  assert.match(page, /src="\/ai-daily\.html"/);
  assert.match(page, /title="AI 日报"/);
  assert.match(layout, /lang="zh-CN"/);
  assert.match(daily, /AI 日报/);
  assert.match(daily, /GitHub 今日高星项目/);
  assert.match(daily, /class="repo-card"/);
  assert.match(daily, /https:\/\/github\.com\/[^"<]+/);
  assert.doesNotMatch(page + layout, /codex-preview|SkeletonPreview|Starter Project/);

  const payloadMatch = daily.match(
    /<script>window\.__TERMS__=(\{.*?\});<\/script>/s,
  );
  assert.ok(payloadMatch, "term payload should be present");

  const payloadSlugs = Object.keys(JSON.parse(payloadMatch[1])).sort();
  const renderedSlugs = [
    ...new Set(
      [...daily.matchAll(/data-slug="([^"]+)"/g)].map((match) => match[1]),
    ),
  ].sort();
  assert.deepEqual(
    payloadSlugs,
    renderedSlugs,
    "the public page must embed only terms that it actually renders",
  );
});
