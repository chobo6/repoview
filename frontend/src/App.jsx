import { useEffect, useState } from 'react'
import { BASE_URL, createSession, fetchRepos, fetchSession } from './api'
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
  const [liveSteps, setLiveSteps] = useState([])
  const [costUsd, setCostUsd] = useState(null)
  const [model, setModel] = useState('')

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
    setLiveSteps([])
    setCostUsd(null)

    try {
      const created = await createSession(repoId, question, model)
      const source = new EventSource(`${BASE_URL}/sessions/${created.session_id}/stream`)

      source.addEventListener('step_started', (e) => {
        const data = JSON.parse(e.data)
        setLiveSteps((prev) => [...prev, `${data.tool_name}(${JSON.stringify(data.tool_args)})`])
      })

      source.addEventListener('assistant_message', (e) => {
        const data = JSON.parse(e.data)
        setLiveSteps((prev) => [...prev, data.text])
      })

      source.addEventListener('done', async (e) => {
        source.close()
        setCostUsd(JSON.parse(e.data).cost_usd)
        try {
          setSession(await fetchSession(created.session_id))
        } catch (err) {
          setError(err.message)
        } finally {
          setLoading(false)
        }
      })

      source.addEventListener('error', (e) => {
        if (!e.data) {
          // 서버가 보낸 진짜 에러가 아니라 순수 연결 문제(네트워크 순단 등) —
          // 세션은 백그라운드에서 계속 실행되고 있으므로 여기서 포기하고 닫아버리면
          // 이미 시작된(돈 드는) 세션 결과를 다시는 볼 수 없게 된다. EventSource의
          // 기본 자동 재연결에 맡긴다 — 재연결하면 서버가 지금까지의 진행 상황을
          // 재생한 뒤 이어서 폴링하도록 이미 설계되어 있다.
          return
        }
        source.close()
        setError(JSON.parse(e.data).message)
        setLoading(false)
      })
    } catch (err) {
      setError(err.message)
      setLoading(false)
    }
  }

  return (
    <main className="app">
      <header className="masthead">
        <h1>RepoView</h1>
      </header>

      <nav className="tabs">
        <button type="button" className={tab === 'ask' ? 'active' : ''} onClick={() => setTab('ask')}>
          질문하기
        </button>
        <button type="button" className={tab === 'evals' ? 'active' : ''} onClick={() => setTab('evals')}>
          Eval 결과
        </button>
      </nav>

      {error && <p className="error">{error}</p>}

      <div className="tab-body">
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

              <select value={model} onChange={(e) => setModel(e.target.value)}>
                <option value="">OpenAI (gpt-4o)</option>
                <option value="qwen2.5:7b">Ollama 로컬 (qwen2.5:7b)</option>
              </select>

              <label htmlFor="question">무엇을 검토할까요?</label>
              <textarea
                id="question"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                placeholder="예: 이 프로젝트에서 성능상 문제가 될 만한 부분 찾아줘"
                rows={3}
              />

              <button type="submit" disabled={loading}>
                {loading ? '분석 중…' : '리뷰 요청'}
              </button>
            </form>

            {liveSteps.length > 0 && (
              <ul className="live-steps">
                {liveSteps.map((line, i) => (
                  <li key={i}>{line}</li>
                ))}
              </ul>
            )}

            {session && (
              <section className="result">
                <h2>리뷰 결과</h2>
                <p className="meta">
                  <span>상태 {session.status}</span>
                  <span>{session.iteration_count}턴</span>
                  <span>토큰 {session.input_tokens + session.output_tokens}</span>
                  {costUsd != null && <span>${costUsd.toFixed(4)}</span>}
                </p>
                <pre className="review">{session.final_review}</pre>

                {parseCitationWarnings(session.citation_warnings).length > 0 && (
                  <div className="citation-warning">
                    <span className="warning-label">
                      인용 검증 경고 {parseCitationWarnings(session.citation_warnings).length}건
                    </span>
                    <ul>
                      {parseCitationWarnings(session.citation_warnings).map((w, i) => (
                        <li key={i}>
                          <span className="stamp">
                            <span>
                              {w.file_path}:{w.start_line}-{w.end_line}
                            </span>
                            <span className="issue">{w.issue}</span>
                          </span>
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
      </div>
    </main>
  )
}

export default App
