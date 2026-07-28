import request from '@/utils/request'

export interface ConnectorAccountStatus {
  account_id: string
  connection_status: string
  last_connected_at?: string | null
  last_message_at?: string | null
  error_code?: string | null
  error_message?: string | null
}

export interface ConnectorDeviceStatus {
  id: number
  device_name: string
  app_version: string
  status: string
  app_status: string
  online: boolean
  last_seen_at?: string | null
  accounts: ConnectorAccountStatus[]
}

export async function getConnectorStatus() {
  const response = await request.get('/api/v1/connectors/status')
  return response.data.data as { devices: ConnectorDeviceStatus[] }
}

export async function getLatestConnectorRelease() {
  const response = await request.get('/api/v1/connectors/release/latest')
  return response.data.data as null | { version: string; download_url: string; sha256: string }
}

export async function revokeConnectorDevice(deviceId: number) {
  return request.post(`/api/v1/connectors/devices/${deviceId}/revoke`)
}

export async function createConnectorBindingCode() {
  const response = await request.post('/api/v1/connectors/binding-codes')
  return response.data.data as { binding_code: string; expires_in_seconds: number }
}
