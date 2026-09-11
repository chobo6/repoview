const BASE_URL = 'http://localhost:8000/api'

export async function fetchRepos() {
  const response = await fetch(`${BASE_URL}/repos`)
  if (!response.ok) throw new Error('레포 목록을 불러오지 못했습니다')
  return response.json()
}

export async function createSession(repoId, question) {
  const response = await fetch(`${BASE_URL}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_id: repoId, question }),
  })
  const body = await response.json()
  if (!response.ok) throw new Error(body.error?.message ?? '요청이 실패했습니다')
  return body
}

export async function fetchSession(sessionId) {
  const response = await fetch(`${BASE_URL}/sessions/${sessionId}`)
  if (!response.ok) throw new Error('세션을 불러오지 못했습니다')
  return response.json()
}
