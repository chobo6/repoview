export async function fetchUsers(): Promise<User[]> {
  const response = await fetch("/api/users");
  return response.json();
}
