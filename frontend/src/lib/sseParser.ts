/**
 * 极简 SSE 解析器（纯逻辑，无 DOM 依赖，便于单元测试）。
 *
 * 后端格式固定为：
 *   event: <type>\n
 *   data: <json>\n
 *   \n
 * 但解析器仍按 SSE 规范容忍注释行、多行 data、CRLF / CR / LF 三种换行，
 * 以及任意位置的 chunk 切分。
 */

export interface ParsedSseEvent {
  /** 事件名；没有 event: 字段时为 "message"。 */
  event: string;
  /** 多行 data 以 "\n" 连接后的原始字符串。 */
  data: string;
  /** 可选的事件 id（后端当前未使用）。 */
  id?: string;
}

const LINE_BREAK = /\r\n|\r|\n/;

export class SseParser {
  private buffer = "";
  private eventType = "";
  private dataLines: string[] = [];
  private lastId: string | undefined;

  /** 喂入一段 chunk，返回其中已经完整的事件。 */
  feed(chunk: string): ParsedSseEvent[] {
    this.buffer += chunk;
    return this.drainCompleteLines();
  }

  /** 流结束时调用：处理没有以空行收尾的最后一个事件。 */
  flush(): ParsedSseEvent[] {
    const events: ParsedSseEvent[] = [];
    if (this.buffer.length > 0) {
      const remainder = this.buffer.replace(/\r$/, "");
      this.buffer = "";
      for (const line of remainder.split(LINE_BREAK)) {
        this.consumeLine(line, events);
      }
    }
    const dispatched = this.dispatch();
    if (dispatched) events.push(dispatched);
    return events;
  }

  private drainCompleteLines(): ParsedSseEvent[] {
    const events: ParsedSseEvent[] = [];

    while (true) {
      const match = LINE_BREAK.exec(this.buffer);
      if (!match) break;
      // 末尾孤立的 \r 可能是 \r\n 的前半段，等下一个 chunk 再判断。
      if (match[0] === "\r" && match.index === this.buffer.length - 1) break;

      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      this.consumeLine(line, events);
    }

    return events;
  }

  private consumeLine(line: string, events: ParsedSseEvent[]): void {
    if (line === "") {
      const dispatched = this.dispatch();
      if (dispatched) events.push(dispatched);
      return;
    }
    if (line.startsWith(":")) return;

    const colon = line.indexOf(":");
    if (colon === -1) return;

    const field = line.slice(0, colon);
    const rawValue = line.slice(colon + 1);
    const value = rawValue.startsWith(" ") ? rawValue.slice(1) : rawValue;

    switch (field) {
      case "event":
        this.eventType = value;
        break;
      case "data":
        this.dataLines.push(value);
        break;
      case "id":
        this.lastId = value;
        break;
      default:
        break;
    }
  }

  private dispatch(): ParsedSseEvent | undefined {
    if (this.dataLines.length === 0) {
      this.eventType = "";
      return undefined;
    }

    const event: ParsedSseEvent = {
      event: this.eventType || "message",
      data: this.dataLines.join("\n"),
    };
    if (this.lastId !== undefined) {
      event.id = this.lastId;
      this.lastId = undefined;
    }

    this.eventType = "";
    this.dataLines = [];
    return event;
  }
}

/** 判断事件名是否为一次 run 的终止事件。 */
export function isTerminalEvent(event: string): boolean {
  return event === "done";
}

/** 解析单条事件块（不含结尾空行），主要便于测试与调试。 */
export function parseSseBlock(block: string): ParsedSseEvent | undefined {
  const parser = new SseParser();
  const events = parser.feed(`${block}\n\n`);
  return events[0];
}
