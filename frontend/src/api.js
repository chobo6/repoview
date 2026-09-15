export const BASE_URL = 'http://localhost:8000/api'

export async function fetchConfig() {
  const response = await fetch(`${BASE_URL}/config`)
  if (!response.ok) throw new Error('설정을 불러오지 못했습니다')
  return response.json()
}

export async function fetchRepos() {
  const response = await fetch(`${BASE_URL}/repos`)
  if (!response.ok) throw new Error('레포 목록을 불러오지 못했습니다')
  return response.json()
}

export async function createSession(repoId, question, model) {
  const body = { repo_id: repoId, question }
  if (model) body.model = model

  const response = await fetch(`${BASE_URL}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  let responseBody
  try {
    responseBody = await response.json()
  } catch {
    if (!response.ok) throw new Error('요청이 실패했습니다')
    throw new Error('응답을 해석하지 못했습니다')
  }
  if (!response.ok) throw new Error(responseBody.error?.message ?? '요청이 실패했습니다')
  return responseBody
}

export async function fetchSession(sessionId) {
  const response = await fetch(`${BASE_URL}/sessions/${sessionId}`)
  if (!response.ok) throw new Error('세션을 불러오지 못했습니다')
  return response.json()
}

export async function fetchEvalRuns(repoId) {
  const response = await fetch(`${BASE_URL}/evals/runs?repo_id=${repoId}`)
  if (!response.ok) throw new Error('eval 결과를 불러오지 못했습니다')
  return response.json()
}

export async function fetchEvalRun(runId) {
  const response = await fetch(`${BASE_URL}/evals/runs/${runId}`)
  if (!response.ok) throw new Error('eval 상세 결과를 불러오지 못했습니다')
  return response.json()
}
