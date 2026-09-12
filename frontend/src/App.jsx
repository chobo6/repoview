import { useEffect, useState } from 'react'
import { createSession, fetchRepos, fetchSession } from './api'
import EvalResults from './EvalResults'
import './App.css'

function parseCitationWarnings(raw) {
  if (!raw) return []
  try {
    return JSON.parse(raw)
  } catch {
    return []
  }
}

function App() {
  const [repos, setRepos] = useState([])
  const [repoId, setRepoId] = useState(null)
  const [question, setQuestion] = useState('')
  const [loading, setLoading] = useState(false)
  const [session, setSession] = useState(null)
  const [error, setError] = useState(null)
  const [tab, setTab] = useState('ask')

  useEffect(() => {
    fetchRepos()
      .then((list) => {
        setRepos(list)
        if (list.length > 0) setRepoId(list[0].id)
      })
      .catch((err) => setError(err.message))
  }, [])

  const handleSubmit = async (event) => {
    event.preventDefault()
    if (!repoId || !question.trim()) return

    setLoading(true)
    setError(null)
    setSession(null)

    try {
      const created = await createSession(repoId, question)
      setSession(await fetchSession(created.session_id))
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="app">
      <h1>RepoView</h1>

      <nav className="tabs">
        <button type="button" className={tab === 'ask' ? 'active' : ''} onClick={() => setTab('ask')}>
          질문하기
        </button>
        <button type="button" className={tab === 'evals' ? 'active' : ''} onClick={() => setTab('evals')}>
          Eval 결과
        </button>
      </nav>

      {tab === 'ask' && (
        <>
          <form onSubmit={handleSubmit}>
            <select value={repoId ?? ''} onChange={(e) => setRepoId(Number(e.target.value))}>
              {repos.map((repo) => (
                <option key={repo.id} value={repo.id}>
                  {repo.name} ({repo.primary_language}, {repo.file_count}개 파일)
                </option>
              ))}
            </select>

            <textarea
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              placeholder="예: 이 프로젝트에서 성능상 문제가 될 만한 부분 찾아줘"
              rows={3}
            />

            <button type="submit" disabled={loading}>
              {loading ? '분석 중…' : '리뷰 요청'}
            </button>
          </form>

          {error && <p className="error">{error}</p>}

          {session && (
            <section className="result">
              <h2>리뷰 결과</h2>
              <p className="meta">
                상태 {session.status} · {session.iteration_count}턴 · 토큰{' '}
                {session.input_tokens + session.output_tokens}
              </p>
              <pre className="review">{session.final_review}</pre>

              {parseCitationWarnings(session.citation_warnings).length > 0 && (
                <div className="citation-warning">
                  ⚠ 인용 검증 경고 {parseCitationWarnings(session.citation_warnings).length}건
                  <ul>
                    {parseCitationWarnings(session.citation_warnings).map((w, i) => (
                      <li key={i}>
                        {w.file_path}:{w.start_line}-{w.end_line} — {w.issue}
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <h3>에이전트 트레이스</h3>
              <ol className="trace">
                {session.trace.map((step) => (
                  <li key={step.id} className={step.error ? 'step error' : 'step'}>
                    <strong>{step.type === 'TOOL_CALL' ? step.tool_name : 'LLM 판단'}</strong>
                    {step.tool_args && <code>{step.tool_args}</code>}
                    {step.tool_result && <pre>{step.tool_result.slice(0, 500)}</pre>}
                    {step.assistant_text && <p>{step.assistant_text}</p>}
                  </li>
                ))}
              </ol>
            </section>
          )}
        </>
      )}

      {tab === 'evals' && <EvalResults repos={repos} />}
    </main>
  )
}

export default App
