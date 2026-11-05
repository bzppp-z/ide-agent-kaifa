export function SseMessage({
  content,
  streaming,
}: {
  content: string;
  streaming: boolean;
}) {
  return (
    <div className="whitespace-pre-wrap break-words text-[13px] leading-[1.65] text-text">
      {content}
      {streaming ? (
        <span className="stream-cursor" aria-hidden="true">
          |
        </span>
      ) : null}
    </div>
  );
}
