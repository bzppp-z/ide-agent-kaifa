import { describe, expect, it } from "vitest";

import {
  SseParser,
  isTerminalEvent,
  parseSseBlock,
} from "../sseParser";

const delta = (content: string) =>
  `event: message.delta\ndata: ${JSON.stringify({ content })}\n\n`;

const done = (steps: number, tokens: number) =>
  `event: done\ndata: ${JSON.stringify({
    run_id: "run-1",
    warning: null,
    total_tokens: tokens,
    total_steps: steps,
  })}\n\n`;

describe("SseParser", () => {
  it("解析单个事件块", () => {
    const events = new SseParser().feed(delta("你好"));
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("message.delta");
    expect(JSON.parse(events[0].data)).toEqual({ content: "你好" });
  });

  it("chunk 在行中间切开时仍能正确拼接", () => {
    const parser = new SseParser();
    const payload = delta("hello world");
    const events = [
      ...parser.feed(payload.slice(0, 9)),
      ...parser.feed(payload.slice(9, 20)),
      ...parser.feed(payload.slice(20)),
    ];
    expect(events).toHaveLength(1);
    expect(JSON.parse(events[0].data)).toEqual({ content: "hello world" });
  });

  it("chunk 恰好切在 \\r\\n 中间时不误判为空行", () => {
    const parser = new SseParser();
    const payload = "event: message.delta\r\ndata: {\"content\":\"x\"}\r\n\r\n";
    const events = [...parser.feed(payload.slice(0, 20)), ...parser.feed(payload.slice(20))];
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("message.delta");
    expect(JSON.parse(events[0].data)).toEqual({ content: "x" });
  });

  it("忽略心跳注释行与额外空行", () => {
    const parser = new SseParser();
    const events = parser.feed(
      ": ping\n\n\n" + delta("a") + ": keep-alive\n\n" + delta("b"),
    );
    expect(events.map((event) => event.event)).toEqual([
      "message.delta",
      "message.delta",
    ]);
  });

  it("一个 chunk 内含多个事件时全部解析（顺序保持）", () => {
    const parser = new SseParser();
    const events = parser.feed(
      delta("a") +
        `event: tool.call\ndata: ${JSON.stringify({
          call_id: "c1",
          tool_name: "list",
          arguments: { path: "src" },
        })}\n\n` +
        done(2, 120),
    );
    expect(events).toHaveLength(3);
    expect(events[1].event).toBe("tool.call");
    expect(events[2].event).toBe("done");
    expect(JSON.parse(events[2].data)).toMatchObject({
      total_steps: 2,
      total_tokens: 120,
    });
  });

  it("多行 data 按规范用换行拼接", () => {
    const events = new SseParser().feed(
      "event: message.delta\ndata: {\"content\":\ndata: \"multi\"}\n\n",
    );
    expect(events).toHaveLength(1);
    expect(JSON.parse(events[0].data)).toEqual({ content: "multi" });
  });

  it("flush 处理没有空行收尾的最后一个事件", () => {
    const parser = new SseParser();
    const events = parser.feed(
      'event: done\ndata: {"run_id":"r","total_steps":1,"total_tokens":2}',
    );
    expect(events).toHaveLength(0);
    const flushed = parser.flush();
    expect(flushed).toHaveLength(1);
    expect(flushed[0].event).toBe("done");
  });

  it("done 被识别为终止事件", () => {
    expect(isTerminalEvent("done")).toBe(true);
    expect(isTerminalEvent("message.delta")).toBe(false);
  });

  it("parseSseBlock 可直接解析单块", () => {
    const parsed = parseSseBlock('event: error\ndata: {"message":"boom"}');
    expect(parsed?.event).toBe("error");
  });
});
