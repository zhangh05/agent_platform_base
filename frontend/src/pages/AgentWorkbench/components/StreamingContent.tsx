interface StreamingContentProps {
  text: string;
}

/**
 * Keep the hot streaming path deliberately cheap.
 *
 * Parsing the entire accumulated answer as Markdown after every token batch
 * makes long tables and code blocks increasingly expensive: each update starts
 * again at byte zero and replaces the complete HTML subtree.  During a stream
 * we therefore update one plain text node only.  MessageRow performs the full
 * Markdown render once, after the terminal frame changes the message to ready.
 */
export function StreamingContent({ text }: StreamingContentProps) {
  return <div className="streaming-plain-text">{text}</div>;
}
