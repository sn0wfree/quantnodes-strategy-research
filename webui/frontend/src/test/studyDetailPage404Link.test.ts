/**
 * Regression test for P1-5: StudyDetailPage 404 fallback link.
 *
 * Pre-fix: the link went to "/" (chat home), which is wrong —
 * the user came from /study and should return to the study list.
 * Post-fix: the link goes to "/study".
 */
import { describe, it, expect } from 'vitest'
import { readFileSync } from 'fs'
import { resolve } from 'path'

function readSource(relPath: string): string {
  // vitest CWD is the webui/frontend root
  return readFileSync(resolve(process.cwd(), relPath), 'utf-8')
}

describe('StudyDetailPage P1-5 404 link regression', () => {
  const source = readSource('src/components/study/StudyDetailPage.tsx')

  it('404 fallback links to /study (not /)', () => {
    // Find the 404 branch — it contains the link with the goto text.
    // We verify the to="/study" prop, not the to="/" (which was the bug).
    const matches404 = source.match(/notFound \|\| !summary[\s\S]*?<\/div>/)

    expect(matches404).not.toBeNull()
    const block = matches404![0]
    expect(block).toContain('to="/study"')
    expect(block).not.toContain('to="/"')
  })

  it('link text says "返回研究列表" (not the old "返回聊天")', () => {
    const matches404 = source.match(/notFound \|\| !summary[\s\S]*?<\/div>/)
    expect(matches404).not.toBeNull()
    expect(matches404![0]).toContain('返回研究列表')
    expect(matches404![0]).not.toContain('返回聊天')
  })

  it('has only one such 404 fallback link (no duplicates)', () => {
    // Avoid regressing to multiple 404 pages
    const matches = source.match(/notFound \|\| !summary/g) ?? []
    expect(matches.length).toBe(1)
  })
})