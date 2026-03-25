import {
  AvatarManager,
  AvatarSDK,
  AvatarView,
  DrivingServiceMode,
  Environment,
  LogLevel,
  type Configuration,
} from '@spatialwalk/avatarkit'

export interface PublicAvatarConfig {
  appId: string
  avatarId: string
  environment: 'cn' | 'intl'
  outputSampleRate: number
  inputSampleRate: number
}

interface AvatarCallbacks {
  onLog: (message: string) => void
  onConversationState: (value: string) => void
}

let avatarView: AvatarView | null = null
let initializedAppId: string | null = null
let audioContextReady = false
const FIRST_FRAME_TIMEOUT_MS = 15000
const CONTAINER_SIZE_WAIT_MS = 500
const CONTAINER_SIZE_CHECK_INTERVAL_MS = 50

function getEnvironment(value: PublicAvatarConfig['environment']): Environment {
  return value === 'intl' ? Environment.intl : Environment.cn
}

async function initializeSdk(config: PublicAvatarConfig): Promise<void> {
  if (initializedAppId === config.appId) {
    return
  }

  const sdkConfig: Configuration = {
    environment: getEnvironment(config.environment),
    drivingServiceMode: DrivingServiceMode.host,
    logLevel: LogLevel.all,
    audioFormat: {
      channelCount: 1,
      sampleRate: config.outputSampleRate,
    },
  }

  await AvatarSDK.initialize(config.appId, sdkConfig)
  initializedAppId = config.appId
}

export async function prepareAvatar(
  config: PublicAvatarConfig,
  callbacks: AvatarCallbacks,
): Promise<AvatarView> {
  const container = document.getElementById('avatar-container')
  if (!(container instanceof HTMLDivElement)) {
    throw new Error('Avatar container not found')
  }

  if (avatarView !== null) {
    if (container.querySelector('canvas') === null) {
      callbacks.onLog('Avatar view cache was stale, recreating view')
      resetAvatar()
    } else {
      return avatarView
    }
  }

  callbacks.onLog(
    `Avatar container size ${container.clientWidth}x${container.clientHeight}`,
  )
  await waitForContainerSize(container, callbacks)
  callbacks.onLog(`Initializing AvatarSDK for ${config.environment}`)
  await initializeSdk(config)

  const avatar = await AvatarManager.shared.load(config.avatarId, (progress) => {
    callbacks.onLog(`Avatar loading ${Math.round(progress.progress ?? 0)}%`)
  })

  const view = new AvatarView(avatar, container)
  avatarView = view
  view.controller.onConversationState = (state) => {
    callbacks.onConversationState(String(state))
  }
  view.controller.onError = (error) => {
    callbacks.onLog(`Avatar error: ${error.message}`)
  }

  try {
    await waitForFirstRendering(view)
  } catch (error) {
    callbacks.onLog(
      `Avatar render init failed with ${container.childElementCount} child nodes`,
    )
    resetAvatar()
    throw error
  }

  callbacks.onLog(`Avatar camera ${JSON.stringify(view.getCameraConfig())}`)
  callbacks.onLog('Avatar first frame rendered')
  return view
}

async function waitForContainerSize(
  container: HTMLDivElement,
  callbacks: Pick<AvatarCallbacks, 'onLog'>,
): Promise<void> {
  let waitedMs = 0
  while (
    (container.clientWidth === 0 || container.clientHeight === 0)
    && waitedMs < CONTAINER_SIZE_WAIT_MS
  ) {
    await new Promise((resolve) => {
      window.setTimeout(resolve, CONTAINER_SIZE_CHECK_INTERVAL_MS)
    })
    waitedMs += CONTAINER_SIZE_CHECK_INTERVAL_MS
  }

  callbacks.onLog(
    `Avatar container settled at ${container.clientWidth}x${container.clientHeight}`,
  )
}

function waitForFirstRendering(view: AvatarView): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => {
      reject(new Error('Avatar first frame timed out'))
    }, FIRST_FRAME_TIMEOUT_MS)

    view.onFirstRendering = () => {
      window.clearTimeout(timer)
      resolve()
    }
  })
}

export async function ensureAudioContext(
  callbacks: Pick<AvatarCallbacks, 'onLog'>,
): Promise<void> {
  if (avatarView === null) {
    throw new Error('Avatar view is not ready')
  }
  if (audioContextReady) {
    return
  }
  await avatarView.controller.initializeAudioContext()
  audioContextReady = true
  callbacks.onLog('Audio context initialized')
}

export function getAvatarView(): AvatarView | null {
  return avatarView
}

export function resetAvatar(): void {
  audioContextReady = false
  if (avatarView !== null) {
    avatarView.dispose()
    avatarView = null
  }
  AvatarSDK.cleanup()
  initializedAppId = null
}
