import { MicrophonePcmCapture } from './audioCapture'
import {
  ensureAudioContext,
  getAvatarView,
  prepareAvatar,
  resetAvatar,
  type PublicAvatarConfig,
} from './avatar'
import './styles.css'

type ServerMessage =
  | {
      type: 'ready'
      sessionId: string
      avatar: PublicAvatarConfig
    }
  | {
      type: 'status'
      message: string
    }
  | {
      type: 'avatar_audio'
      turnId: string
      audio: string
      isLast: boolean
    }
  | {
      type: 'avatar_frames'
      turnId: string
      frames: string[]
      isLast: boolean
    }
  | {
      type: 'interrupt'
      reason: string
    }
  | {
      type: 'agent_event'
      event: string
      turnId?: string
      text?: string
      payload?: unknown
      isSoftFinished?: boolean
    }
  | {
      type: 'error'
      message: string
    }
  | {
      type: 'pong'
    }

interface ClientMessage {
  type: string
  [key: string]: unknown
}

const prepareButton = getRequired<HTMLButtonElement>('prepare-button')
const micButton = getRequired<HTMLButtonElement>('mic-button')
const promptForm = getRequired<HTMLFormElement>('prompt-form')
const promptInput = getRequired<HTMLInputElement>('prompt-input')
const promptSubmit = getRequired<HTMLButtonElement>('prompt-submit')
const statusText = getRequired<HTMLDivElement>('status-text')
const connectionPill = getRequired<HTMLDivElement>('connection-pill')

let publicConfig: PublicAvatarConfig | null = null
let socket: WebSocket | null = null
let microphone: MicrophonePcmCapture | null = null
let isMicStreaming = false

const turnMap = new Map<string, string>()

setConnectionState('offline')
setMicState(false)

async function bootstrap(): Promise<void> {
  publicConfig = await fetchConfig()
  setStatus('Config loaded, ready to initialize', 'idle')
  appendLog('Loaded backend config')

  prepareButton.addEventListener('click', async () => {
    try {
      await prepareRuntime()
    } catch (error) {
      handleError(error)
    }
  })

  micButton.addEventListener('click', async () => {
    try {
      if (!isMicStreaming) {
        await startMicrophone()
      } else {
        await stopMicrophone()
      }
    } catch (error) {
      handleError(error)
    }
  })

  promptForm.addEventListener('submit', async (event) => {
    event.preventDefault()
    try {
      await prepareRuntime()
      const text = promptInput.value.trim()
      if (!text || socket === null) {
        return
      }
      sendMessage({ type: 'text_query', text })
      appendLog(`Text query -> ${text}`)
      promptInput.value = ''
    } catch (error) {
      handleError(error)
    }
  })

  window.addEventListener('beforeunload', () => {
    socket?.close()
    void microphone?.stop()
    resetAvatar()
  })
}

async function prepareRuntime(): Promise<void> {
  if (publicConfig === null) {
    throw new Error('Public config is not ready')
  }

  prepareButton.disabled = true
  setStatus('Initializing AvatarKit', 'pending')

  try {
    await prepareAvatar(publicConfig, {
      onLog: appendLog,
      onConversationState: (value) => {
        setConnectionState(value)
      },
    })
    await ensureAudioContext({ onLog: appendLog })
    await connectSocket()
  } catch (error) {
    prepareButton.disabled = false
    throw error
  }

  micButton.disabled = false
  promptSubmit.disabled = false
  setStatus('Avatar and WebSocket ready', 'ready')
  setConnectionState('ready')
}

async function startMicrophone(): Promise<void> {
  if (publicConfig === null) {
    throw new Error('Public config is not ready')
  }
  await prepareRuntime()
  if (socket === null) {
    throw new Error('WebSocket is not connected')
  }

  microphone = microphone ?? new MicrophonePcmCapture(publicConfig.inputSampleRate)
  await microphone.start((chunk) => {
    if (socket?.readyState === WebSocket.OPEN) {
      sendMessage({ type: 'mic_audio', audio: bytesToBase64(chunk) })
    }
  })
  isMicStreaming = true
  setMicState(true)
  micButton.textContent = 'Stop Mic'
  setStatus('Microphone live', 'live')
  appendLog('Microphone capture started')
}

async function stopMicrophone(): Promise<void> {
  if (microphone !== null) {
    await microphone.stop()
    microphone = null
  }
  isMicStreaming = false
  setMicState(false)
  micButton.textContent = 'Mic'
  sendMessage({ type: 'mic_end' })
  setStatus('Microphone off', 'idle')
  appendLog('Microphone capture stopped')
}

async function connectSocket(): Promise<void> {
  if (socket !== null && socket.readyState === WebSocket.OPEN) {
    return
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  socket = new WebSocket(`${protocol}//${window.location.host}/ws/agent`)

  await new Promise<void>((resolve, reject) => {
    const currentSocket = socket
    if (currentSocket === null) {
      reject(new Error('WebSocket did not initialize'))
      return
    }
    currentSocket.addEventListener('open', () => {
      appendLog('Browser WebSocket connected')
      resolve()
    }, { once: true })
    currentSocket.addEventListener('error', () => {
      reject(new Error('Failed to connect browser WebSocket'))
    }, { once: true })
  })

  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data) as ServerMessage
    void handleServerMessage(message).catch(handleError)
  })
  socket.addEventListener('close', () => {
    setConnectionState('offline')
    setMicState(false)
    appendLog('Browser WebSocket closed')
  })
}

async function handleServerMessage(message: ServerMessage): Promise<void> {
  switch (message.type) {
    case 'ready':
      appendLog(`Server session ready: ${message.sessionId}`)
      return
    case 'status':
      setStatus(message.message, inferStatusTone(message.message))
      appendLog(message.message)
      return
    case 'avatar_audio':
      await handleAvatarAudio(message.turnId, message.audio, message.isLast)
      return
    case 'avatar_frames':
      handleAvatarFrames(message.turnId, message.frames, message.isLast)
      return
    case 'interrupt':
      getAvatarView()?.controller.interrupt()
      turnMap.clear()
      setStatus(`Session interrupted: ${message.reason}`, 'warning')
      appendLog(`Interrupt <- ${message.reason}`)
      return
    case 'agent_event':
      handleAgentEvent(message)
      return
    case 'error':
      throw new Error(message.message)
    case 'pong':
      return
  }
}

async function handleAvatarAudio(
  turnId: string,
  audioBase64: string,
  isLast: boolean,
): Promise<void> {
  const avatarView = getAvatarView()
  if (avatarView === null) {
    return
  }

  const audioData = base64ToBytes(audioBase64)
  const localConversationId = avatarView.controller.yieldAudioData(audioData, isLast)
  if (!turnMap.has(turnId) && localConversationId !== null) {
    turnMap.set(turnId, localConversationId)
  }
  if (isLast) {
    appendLog(`Avatar audio end <- ${turnId}`)
  }
}

function handleAvatarFrames(
  turnId: string,
  framePayloads: string[],
  isLast: boolean,
): void {
  const avatarView = getAvatarView()
  const localConversationId = turnMap.get(turnId)
  if (avatarView === null || localConversationId === undefined) {
    return
  }

  const frames = framePayloads.map((value) => base64ToBytes(value))
  avatarView.controller.yieldFramesData(frames, localConversationId)
  if (isLast) {
    turnMap.delete(turnId)
    appendLog(`Avatar frames end <- ${turnId}`)
  }
}

function handleAgentEvent(message: Extract<ServerMessage, { type: 'agent_event' }>): void {
  appendLog(`Agent event <- ${message.event}${message.text ? `: ${message.text}` : ''}`)
}

async function fetchConfig(): Promise<PublicAvatarConfig> {
  const response = await fetch('/api/config')
  if (!response.ok) {
    const payload = await response.json()
    throw new Error(`Backend config error: ${JSON.stringify(payload)}`)
  }
  return (await response.json()) as PublicAvatarConfig
}

function sendMessage(message: ClientMessage): void {
  if (socket === null || socket.readyState !== WebSocket.OPEN) {
    return
  }
  socket.send(JSON.stringify(message))
}

function setStatus(value: string, tone: 'idle' | 'pending' | 'ready' | 'live' | 'warning' | 'error'): void {
  statusText.textContent = value
  statusText.dataset.tone = tone
}

function setConnectionState(value: string): void {
  const normalized = normalizeConnectionState(value)
  connectionPill.textContent = value
  connectionPill.dataset.state = normalized
  document.body.dataset.connection = normalized
}

function setMicState(active: boolean): void {
  document.body.dataset.mic = active ? 'on' : 'off'
}

function appendLog(message: string): void {
  const timestamp = new Date().toLocaleTimeString('en-GB', { hour12: false })
  console.debug(`[hostmode-demo][${timestamp}] ${message}`)
}

function handleError(error: unknown): void {
  const message = error instanceof Error ? error.message : String(error)
  setStatus(`Error: ${message}`, 'error')
  appendLog(`Error: ${message}`)
  setConnectionState('error')
  setMicState(false)
}

function normalizeConnectionState(value: string): 'offline' | 'ready' | 'live' | 'error' | 'busy' {
  const lowered = value.toLowerCase()
  if (lowered.includes('error') || lowered.includes('fail')) {
    return 'error'
  }
  if (lowered.includes('offline') || lowered.includes('closed')) {
    return 'offline'
  }
  if (lowered.includes('speak') || lowered.includes('talk') || lowered.includes('listen') || lowered.includes('mic')) {
    return 'live'
  }
  if (lowered.includes('ready') || lowered.includes('idle')) {
    return 'ready'
  }
  return 'busy'
}

function inferStatusTone(value: string): 'idle' | 'pending' | 'ready' | 'live' | 'warning' | 'error' {
  const lowered = value.toLowerCase()
  if (lowered.includes('error') || lowered.includes('fail')) {
    return 'error'
  }
  if (lowered.includes('interrupt')) {
    return 'warning'
  }
  if (lowered.includes('listen') || lowered.includes('speak') || lowered.includes('stream') || lowered.includes('mic')) {
    return 'live'
  }
  if (lowered.includes('ready') || lowered.includes('done') || lowered.includes('complete')) {
    return 'ready'
  }
  if (lowered.includes('init') || lowered.includes('connect')) {
    return 'pending'
  }
  return 'idle'
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  for (const byte of bytes) {
    binary += String.fromCharCode(byte)
  }
  return btoa(binary)
}

function base64ToBytes(value: string): Uint8Array {
  if (!value) {
    return new Uint8Array(0)
  }
  const binary = atob(value)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return bytes
}

function getRequired<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id)
  if (element === null) {
    throw new Error(`Missing element #${id}`)
  }
  return element as T
}

void bootstrap()
