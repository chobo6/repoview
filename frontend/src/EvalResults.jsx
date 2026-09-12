import { useEffect, useState } from 'react'
import { fetchEvalRun, fetchEvalRuns, fetchSession } from './api'

function formatPercent(value) {
  return value == null ? '-' : `${Math.round(value * 100)}%`
}

function EvalResults({ repos }) {
  const [repoId, setRepoId] = useState(repos[0]?.id ?? null)
  const [runs, setRuns] = useState([])
  const [selectedRun, setSelectedRun] = useState(null)
  const [session, setSession] = useState(null)
  const [error, setError] = useState(null)

  // repos는 App.jsx가 마운트 후 비동기로 채운다 — "Eval 결과" 탭을 그 전에 열면
  // repoId가 null로 굳어 목록이 영영 안 뜨므로, repos가 나중에 채워지면 다시 맞춘다.
  useEffect(() => {
    if (repoId == null && repos.length > 0) setRepoId(repos[0].id)
  }, [repos, repoId])

  useEffect(() => {
    if (!repoId) return
    setSelectedRun(null)
    setSession(null)
    setError(null)
    fetchEvalRuns(repoId)
      .then(setRuns)
      .catch((err) => setError(err.message))
  }, [repoId])

  const handleSelectRun = async (runId) => {
    setSession(null)
    setError(null)
    try {
      setSelectedRun(await fetchEvalRun(runId))
    } catch (err) {
      setError(err.message)
    }
  }

  const handleViewSession = async (sessionId) => {
    if (!sessionId) return
    setError(null)
    try {
      setSession(await fetchSession(sessionId))
    } catch (err) {
      setError(err.message)
    }
  }

  return (
    <div className="eval-results">
      <select value={repoId ?? ''} onChange={(e) => setRepoId(Number(e.target.value))}>
        {repos.map((repo) => (
          <option key={repo.id} value={repo.id}>
            {repo.name}
          </option>
        ))}
      </select>

      {error && <p className="error">{error}</p>}

      <table className="eval-table">
        <thead>
          <tr>
            <th>Phase</th>
            <th>모델</th>
            <th>탐지율</th>
            <th>오탐율</th>
            <th>인용정확도</th>
            <th>비용</th>
            <th>지연</th>
            <th>실행시각</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr
              key={run.id}
              className={selectedRun?.id === run.id ? 'selected' : ''}
              onClick={() => handleSelectRun(run.id)}
            >
              <td>{run.phase}</td>
              <td>{run.model}</td>
              <td>
                {formatPercent(run.detection_rate)}
                {run.total_cases != null && ` (${run.passed_cases}/${run.total_cases})`}
              </td>
              <td>{formatPercent(run.fpr)}</td>
              <td>{formatPercent(run.citation_accuracy)}</td>
              <td>{run.avg_cost_usd != null ? `$${run.avg_cost_usd.toFixed(4)}` : '-'}</td>
              <td>{run.avg_latency_ms != null ? `${Math.round(run.avg_latency_ms)}ms` : '-'}</td>
              <td>{run.started_at}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {selectedRun && (
        <section className="eval-detail">
          <h3>케이스별 결과</h3>
          <ul>
            {selectedRun.results.map((result) => (
              <li key={result.eval_case_id} className={result.false_positive ? 'false-positive' : ''}>
                <p>
                  <strong>{result.question}</strong> ({result.category}
                  {result.is_planted ? ', 심은 버그' : ''})
                </p>
                <p>탐지: {result.detected ? 'O' : 'X'} · 오탐: {result.false_positive ? 'O' : 'X'}</p>
                <p className="meta">{result.judge_reason}</p>
                {result.session_id && (
                  <button type="button" onClick={() => handleViewSession(result.session_id)}>
                    세션 보기
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      {session && (
        <section className="result">
          <h3>세션 리뷰</h3>
          <pre className="review">{session.final_review}</pre>
        </section>
      )}
    </div>
  )
}

export default EvalResults
