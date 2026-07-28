import { useEffect, useState } from 'react'
import { createConnectorBindingCode, getConnectorStatus, getLatestConnectorRelease, revokeConnectorDevice, type ConnectorDeviceStatus } from '@/api/connectors'

export function Accounts() {
  const [devices, setDevices] = useState<ConnectorDeviceStatus[]>([])
  const [downloadUrl, setDownloadUrl] = useState<string>('')
  const [message, setMessage] = useState('正在读取本地连接器状态…')
  const [bindingCode, setBindingCode] = useState('')

  const refresh = async () => {
    try {
      const [status, release] = await Promise.all([getConnectorStatus(), getLatestConnectorRelease()])
      setDevices(status.devices || [])
      setDownloadUrl(release?.download_url || '')
      setMessage(status.devices?.length ? '' : '尚未绑定设备，请先下载安装本地连接器并登录绑定。')
    } catch (error) {
      setMessage(`状态读取失败：${error instanceof Error ? error.message : '未知错误'}`)
    }
  }

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), 30000)
    return () => window.clearInterval(timer)
  }, [])

  const createCode = async () => {
    const result = await createConnectorBindingCode()
    setBindingCode(result.binding_code)
  }

  const revoke = async (deviceId: number) => {
    if (!window.confirm('吊销后该设备会立即停止自动回复，确认继续？')) return
    await revokeConnectorDevice(deviceId)
    await refresh()
  }

  return <div className="page-container">
    <div className="page-header">
      <div><h1 className="page-title">本地连接器</h1><p className="page-description">Cookie、Token 和密码只保存在你的电脑，云端不再提供闲鱼直连。</p></div>
      {downloadUrl ? <a className="btn btn-primary" href={downloadUrl}>下载 Windows 连接器</a> : <button className="btn btn-primary" disabled>安装包尚未发布</button>}
    </div>
    <div className="card" style={{ padding: 20, marginBottom: 16 }}>
      <strong>设备绑定</strong>
      <p>生成一次性绑定码并在本地连接器中输入，10 分钟内有效，不上传密码。</p>
      <button className="btn btn-primary" onClick={() => void createCode()}>生成绑定码</button>
      {bindingCode && <code style={{ marginLeft: 12, userSelect: 'all' }}>{bindingCode}</code>}
    </div>
    <div className="card" style={{ padding: 20, marginBottom: 16 }}>
      <strong>运行规则</strong>
      <p>电脑或本地连接器离线时，自动回复明确暂停；系统不会切换到云端连接。</p>
    </div>
    {message && <div className="card" style={{ padding: 20 }}>{message}</div>}
    {devices.map(device => <div className="card" style={{ padding: 20, marginBottom: 16 }} key={device.id}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16 }}>
        <div><h3>{device.device_name}</h3><p>{device.online ? '设备在线' : '设备离线'} · 连接器 {device.app_status} · v{device.app_version}</p></div>
        {device.status === 'active' && <button className="btn btn-danger" onClick={() => void revoke(device.id)}>解绑并吊销</button>}
      </div>
      {device.accounts.map(account => <div key={account.account_id} style={{ borderTop: '1px solid var(--border-color)', paddingTop: 12, marginTop: 12 }}>
        <strong>闲鱼账号 {account.account_id}</strong>
        <p>状态：{account.connection_status}{account.error_message ? ` · ${account.error_message}` : ''}</p>
      </div>)}
    </div>)}
  </div>
}
