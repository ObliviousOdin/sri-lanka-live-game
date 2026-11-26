import type { AppData } from '../data/useData'

// TODO: implemented by a panel agent (see docs/PLAN.md §6)
export function Explorer({ app }: { app: AppData }) {
  void app
  return <div className="panel-empty">Explorer — coming soon</div>
}
