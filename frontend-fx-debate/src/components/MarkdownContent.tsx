import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

export interface MarkdownContentProps {
  children: string;
  className?: string;
}

const components: Components = {
  table: ({ children }) => <div className="markdown-table-wrap"><table>{children}</table></div>,
  a: ({ children, href, node: _node, ...props }) => <a {...props} href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  pre: ({ children, node: _node, ...props }) => <pre {...props} className="markdown-code-block">{children}</pre>,
  input: ({ checked, node: _node, ...props }) => <input {...props} type="checkbox" checked={Boolean(checked)} disabled readOnly />,
};

export function MarkdownContent({ children, className }: MarkdownContentProps) {
  const rootClassName = ["markdown-content", className].filter(Boolean).join(" ");
  return <ReactMarkdown className={rootClassName} components={components} remarkPlugins={[remarkGfm]} skipHtml>{children}</ReactMarkdown>;
}
