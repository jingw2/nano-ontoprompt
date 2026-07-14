import { Outlet } from 'react-router-dom'

export default function PipelinesLayout() {
  return (
    <div>
      <h1 className="text-2xl font-bold mb-4">数据管道</h1>
      <Outlet />
    </div>
  )
}
