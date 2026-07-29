import { describe, it, expect } from 'vitest'
// Note: react-syntax-highlighter is mocked globally in test/setup.ts
import { render } from '@testing-library/react'
import { MarkdownRenderer } from '../components/chat/MarkdownRenderer'

describe('MarkdownRenderer', () => {
  it('renders plain text', () => {
    const { container } = render(<MarkdownRenderer content="Hello world" />)
    expect(container.textContent).toContain('Hello world')
  })

  it('renders headings', () => {
    const { container } = render(
      <MarkdownRenderer content={'# Title\n## Subtitle'} />
    )
    expect(container.querySelector('h1')?.textContent).toContain('Title')
    expect(container.querySelector('h2')?.textContent).toContain('Subtitle')
  })

  it('renders unordered lists', () => {
    const { container } = render(
      <MarkdownRenderer content={'- Item 1\n- Item 2\n- Item 3'} />
    )
    const items = container.querySelectorAll('li')
    expect(items.length).toBe(3)
    expect(items[0].textContent).toContain('Item 1')
  })

  it('renders inline code', () => {
    const { container } = render(
      <MarkdownRenderer content={'Use `npm install` to install'} />
    )
    const inlineCode = container.querySelector('code')
    expect(inlineCode?.textContent).toContain('npm install')
  })

  it('renders fenced code blocks with language', () => {
    const { container } = render(
      <MarkdownRenderer content={'```python\nprint("hi")\n```'} />
    )
    expect(container.textContent).toContain('print')
    // CodeBlock header shows language
    expect(container.textContent).toContain('python')
  })

  it('renders GFM tables', () => {
    const { container } = render(
      <MarkdownRenderer
        content={'| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |'}
      />
    )
    const cells = container.querySelectorAll('td')
    expect(cells.length).toBe(4)
  })

  it('renders blockquotes', () => {
    const { container } = render(
      <MarkdownRenderer content={'> A wise quote'} />
    )
    const bq = container.querySelector('blockquote')
    expect(bq?.textContent).toContain('A wise quote')
  })

  it('renders links with target=_blank', () => {
    const { container } = render(
      <MarkdownRenderer content={'[OpenAI](https://openai.com)'} />
    )
    const link = container.querySelector('a')
    expect(link?.getAttribute('href')).toBe('https://openai.com')
    expect(link?.getAttribute('target')).toBe('_blank')
    expect(link?.getAttribute('rel')).toContain('noopener')
  })

  it('renders bold and italic', () => {
    const { container } = render(
      <MarkdownRenderer content={'**bold** and *italic*'} />
    )
    expect(container.querySelector('strong')?.textContent).toContain('bold')
    expect(container.querySelector('em')?.textContent).toContain('italic')
  })

  it('renders horizontal rule', () => {
    const { container } = render(<MarkdownRenderer content="---" />)
    expect(container.querySelector('hr')).toBeTruthy()
  })
})