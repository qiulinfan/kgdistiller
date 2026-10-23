import { describe, expect, it } from "vitest";
import { acceptRanking, rankSourceLinks, type RankingInput } from "../src/origin-ranking";
import type { ChatReply, ChatRequest } from "../src/model";

const input: RankingInput = {
  term: "coding network", context: "阅读 coding network 的定义。",
  candidates: Array.from({ length: 18 }, (_, n) => ({ title: `Work ${n + 1}`, url: `https://publisher.example/work-${n + 1}`, context: "A Wikipedia reference; primary page not read." })),
};

describe("source reranking", () => {
  it("preserves semantic ranking order, including candidates beyond the first ten", () => {
    expect(acceptRanking({ sources: [{ number: 18, annotation: "最接近所选概念的早期工作；尚未读取原文。" }, { number: 3, annotation: "背景解释；来自 Wikipedia 引用。" }] }, input.candidates))
      .toEqual([{ title: "Work 18", url: "https://publisher.example/work-18", annotation: "最接近所选概念的早期工作；尚未读取原文。" }, { title: "Work 3", url: "https://publisher.example/work-3", annotation: "背景解释；来自 Wikipedia 引用。" }]);
  });

  it("refuses oversized lists instead of silently truncating a bibliography", () => {
    expect(acceptRanking({ sources: Array.from({ length: 11 }, (_, n) => ({ number: n + 1, annotation: "Related." })) }, input.candidates)).toContain("at most 10");
  });

  it("refuses missing annotations, invented candidates and duplicate URLs", () => {
    expect(acceptRanking({ sources: [{ number: 1, annotation: " " }] }, input.candidates)).toContain("annotation");
    expect(acceptRanking({ sources: [{ number: 100, annotation: "Related." }] }, input.candidates)).toContain("observed candidate");
    expect(acceptRanking({ sources: [{ number: 1, annotation: "One." }, { number: 1, annotation: "Again." }] }, input.candidates)).toContain("only once");
    expect(acceptRanking({ sources: [null] }, input.candidates)).toContain("observed candidate");
  });

  it("allows fewer than ten relevant results without filling a quota", () => {
    expect(acceptRanking({ sources: [] }, input.candidates)).toEqual([]);
  });

  it("asks the model to rerank the whole candidate pool and correct an unannotated link", async () => {
    const requests: ChatRequest[] = [];
    const answers = [{ sources: [{ number: 18, annotation: "" }] }, { sources: [{ number: 18, annotation: "解释 coding network；只核实到引用线索。" }] }];
    const complete = async (request: ChatRequest): Promise<ChatReply> => {
      requests.push(structuredClone(request));
      return { message: { content: null, tool_calls: [{ id: "rank", type: "function", function: { name: "rank", arguments: JSON.stringify(answers.shift()) } }] } };
    };
    const guidance = "Descriptions use Chinese, technical terms remain in English.";
    expect(await rankSourceLinks(complete, { ...input, guidance })).toEqual([{ title: "Work 18", url: "https://publisher.example/work-18", annotation: "解释 coding network；只核实到引用线索。" }]);
    expect(requests[0]!.messages[1]!.content).toContain("[18] Work 18");
    expect(requests[0]!.tool_choice).toEqual({ type: "function", function: { name: "rank" } });
    expect(requests[0]!.messages[1]!.content).toContain(guidance);
    expect(requests[1]!.messages.at(-1)!.content).toContain("annotation");
  });
});
