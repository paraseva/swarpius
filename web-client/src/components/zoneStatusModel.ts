export interface ZoneNowPlaying {
  line1?: string
  line2?: string
  line3?: string
  length?: number
}

export interface OutputVolumeInfo {
  name: string
  value: number | null
  type: string | null
  is_muted: boolean
  min: number
  max: number
  step: number
}

export type RepeatMode = 'disabled' | 'loop' | 'loop_one'

export interface SnapshotZone {
  zone_id?: string
  display_name?: string
  zone_alias?: string | null
  group_name?: string | null
  state?: string
  seek_position?: number
  queue_items_remaining?: number
  queue_time_remaining?: number
  shuffle?: boolean
  loop?: RepeatMode
  auto_radio?: boolean
  is_grouped?: boolean
  group_members?: string[]
  outputs_volume?: OutputVolumeInfo[]
  image_key?: string | null
  now_playing?: ZoneNowPlaying
}

export interface ZoneSnapshotEvent {
  source?: string
  data?: {
    zones?: SnapshotZone[]
    timestamp_ms?: number
  }
}

export interface ImageResponsePayload {
  request_id?: string
  ok?: boolean
  image_key?: string
  width?: number
  height?: number
  mime_type?: string
  base64_data?: string
  error?: string
}

export interface ZoneArtworkState {
  zoneId: string
  displayName: string
  zoneAlias?: string | null
  groupName?: string | null
  state: string
  seekPosition: number
  queueTimeRemaining?: number
  imageKey: string | null
  nowPlaying: ZoneNowPlaying
  lastUpdatedAt: number
  artDataUri?: string
  isGrouped?: boolean
  groupMembers?: string[]
  outputsVolume: OutputVolumeInfo[]
  shuffle: boolean
  loop: RepeatMode
  autoRadio: boolean
  imageLoadFailed?: boolean
  imageRequestStartedAt?: number
  // Seconds (Date.now() / 1000) when the zone first entered stopped.
  // Undefined while non-stopped; 0 when first seen stopped (filtered
  // out immediately, no grace).
  stoppedSince?: number
}

export interface QueueItem {
  queue_item_id: number
  length: number
  image_key: string | null
  one_line?: { line1: string }
  two_line?: { line1: string; line2: string }
  three_line?: { line1: string; line2: string; line3: string }
}

export interface QueueUpdatePayload {
  zone_id: string
  zone_display_name: string
  items: QueueItem[]
}

export interface RoonControlResponsePayload {
  request_id?: string
  ok?: boolean
  action?: string
  zone?: string | null
  error?: string
}

export const DEFAULT_ART_WIDTH = 512
export const DEFAULT_ART_HEIGHT = 512
export const FINISHED_ZONE_IDLE_SECONDS = 2
export const TRACK_END_TOLERANCE_SECONDS = 1
export const ARTWORK_PENDING_TIMEOUT_MS = 6000
