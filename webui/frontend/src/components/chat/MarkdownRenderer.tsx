import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { Copy, Check } from 'lucide-react'
import { useState, useCallback } from 'react'

interface MarkdownRendererProps {
  content: string
}

function CodeBlock({ language, children }: { language: string; children: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(() => {
    navigator.clipboard.writeText(children)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [children])

  return (
    <div className="group relative my-2 rounded-lg overflow-hidden border border-slate-700/50">
      <div className="flex items-center justify-between bg-slate-800 px-4 py-1.5 text-xs text-slate-400">
        <span>{language || 'code'}</span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-slate-500 hover:text-slate-300 opacity-0 group-hover:opacity-100 transition-opacity"
        >
          {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
          {copied ? '已复制' : '复制'}
        </button>
      </div>
      <SyntaxHighlighter
        language={language || 'text'}
        style={oneDark}
        customStyle={{ margin: 0, borderRadius: 0, fontSize: '0.8125rem' }}
      >
        {children}
      </SyntaxHighlighter>
    </div>
  )
}

export function MarkdownRenderer({ content }: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || '')
          const codeStr = String(children).replace(/\n$/, '')
          if (match) {
            return <CodeBlock language={match[1]} children={codeStr} />
          }
          return (
            <code
              className="rounded bg-slate-800 px-1.5 py-0.5 text-sm font-mono text-primary-300"
              {...props}
            >
              {children}
            </code>
          )
        },
        table({ children }) {
          return (
            <div className="my-2 overflow-x-auto rounded-lg border border-slate-700/50">
              <table className="w-full text-sm">{children}</table>
            </div>
          )
        },
        thead({ children }) {
          return <thead className="bg-slate-800/50">{children}</thead>
        },
        th({ children }) {
          return (
            <th className="px-3 py-2 text-left font-medium text-slate-300 border-b border-slate-700/50">
              {children}
            </th>
          )
        },
        td({ children }) {
          return (
            <td className="px-3 py-2 border-b border-slate-800/50 text-slate-300">
              {children}
            </td>
          )
        },
        p({ children }) {
          return <p className="my-1.5 leading-relaxed">{children}</p>
        },
        ul({ children }) {
          return <ul className="my-1.5 list-disc pl-5 space-y-1">{children}</ul>
        },
        ol({ children }) {
          return <ol className="my-1.5 list-decimal pl-5 space-y-1">{children}</ol>
        },
        li({ children }) {
          return <li className="leading-relaxed">{children}</li>
        },
        blockquote({ children }) {
          return (
            <blockquote className="my-2 border-l-4 border-primary-500/50 pl-4 text-slate-400 italic">
              {children}
            </blockquote>
          )
        },
        h1({ children }) {
          return <h1 className="my-3 text-xl font-bold text-slate-100">{children}</h1>
        },
        h2({ children }) {
          return <h2 className="my-2.5 text-lg font-semibold text-slate-100">{children}</h2>
        },
        h3({ children }) {
          return <h3 className="my-2 text-base font-semibold text-slate-200">{children}</h3>
        },
        a({ href, children }) {
          return (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary-400 hover:underline"
            >
              {children}
            </a>
          )
        },
        hr() {
          return <hr className="my-4 border-slate-700/50" />
        },
      }}
    >
      {content}
    </ReactMarkdown>
  )
}
