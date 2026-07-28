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

export interface ConnectorPairingInfo {
  state: string
  status: string
  device_name: string
  platform: string
  app_version: string
  expires_at: string
}

export async function getConnectorPairing(state: string) {
  const response = await request.get(`/api/v1/connectors/pairing-sessions/by-state/${encodeURIComponent(state)}`)
  return response.data.data as ConnectorPairingInfo
}

export async function approveConnectorPairing(state: string) {
  const response = await request.post(`/api/v1/connectors/pairing-sessions/by-state/${encodeURIComponent(state)}/approve`)
  return response.data.data as { status: string }
}
