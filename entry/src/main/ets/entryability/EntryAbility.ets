import UIAbility from '@ohos.app.ability.UIAbility';
import window from '@ohos.window';
import { BusinessError } from '@ohos.base';
import { backgroundTaskManager } from '@kit.BackgroundTasksKit';
import { wantAgent, WantAgent } from '@kit.AbilityKit';
import { emitter } from '@kit.BasicServicesKit';
import { BackgroundState } from '../model/BackgroundState';

const BG_EVENT_ID = 100;

export default class EntryAbility extends UIAbility {
  private isLongTaskRunning: boolean = false;
  private isLongTaskStartPending: boolean = false;
  private isLongTaskStopPending: boolean = false;
  private longTaskStopRetryCount: number = 0;
  private isInBackground: boolean = false;
  private longTaskGeneration: number = 0;
  private collectionHandlerRegistered: boolean = false;
  private mainWindow?: window.Window;
  private readonly collectionEventHandler: (data: emitter.EventData) => void = (data: emitter.EventData): void => {
    const actionData: Record<string, string> = data.data as Record<string, string>;
    if (actionData && actionData.action === 'collectionStateChanged') {
      this.reconcileBackgroundCollectionState();
      this.updateKeepScreenOn();
    }
  };

  onBackground(): void {
    this.isInBackground = true;
    this.updateKeepScreenOn();
    if (BackgroundState.isCollectionActive()) {
      this.startLongTask();
      return;
    }
    emitter.emit({ eventId: BG_EVENT_ID }, {
      data: { action: 'stopSensors' }
    });
  }

  onForeground(): void {
    this.isInBackground = false;
    this.longTaskGeneration += 1;
    this.stopLongTask();
    this.updateKeepScreenOn();
    emitter.emit({ eventId: BG_EVENT_ID }, {
      data: { action: 'restoreSensors' }
    });
  }

  private reconcileBackgroundCollectionState(): void {
    if (!this.isInBackground) {
      return;
    }
    if (BackgroundState.isCollectionActive()) {
      this.startLongTask();
      return;
    }
    this.longTaskGeneration += 1;
    this.stopLongTask();
  }

  private restartLongTaskIfNeeded(): void {
    if (this.isInBackground && BackgroundState.isCollectionActive() &&
      !this.isLongTaskRunning && !this.isLongTaskStartPending) {
      this.startLongTask();
    }
  }

  private startLongTask(): void {
    if (this.isLongTaskRunning || this.isLongTaskStartPending) {
      return;
    }
    this.isLongTaskStartPending = true;
    const generation = ++this.longTaskGeneration;
    const wantAgentInfo: wantAgent.WantAgentInfo = {
      wants: [
        {
          bundleName: 'com.codex.metrospeed',
          abilityName: 'EntryAbility'
        }
      ],
      actionType: wantAgent.OperationType.START_ABILITY,
      requestCode: 0,
      actionFlags: [wantAgent.WantAgentFlags.UPDATE_PRESENT_FLAG]
    };

    wantAgent.getWantAgent(wantAgentInfo).then((agent: WantAgent) => {
      if (!this.shouldKeepLongTask(generation)) {
        this.isLongTaskStartPending = false;
        this.restartLongTaskIfNeeded();
        return;
      }
      backgroundTaskManager.startBackgroundRunning(
        this.context,
        backgroundTaskManager.BackgroundMode.LOCATION,
        agent
      ).then(() => {
        this.isLongTaskStartPending = false;
        this.isLongTaskRunning = true;
        this.longTaskStopRetryCount = 0;
        if (!this.shouldKeepLongTask(generation)) {
          this.stopLongTask();
        }
      }).catch((err: BusinessError) => {
        this.isLongTaskStartPending = false;
        console.error(`Long task start failed: ${err.message}`);
        if (this.shouldKeepLongTask(generation)) {
          emitter.emit({ eventId: BG_EVENT_ID }, {
            data: { action: 'stopSensors' }
          });
        } else {
          this.restartLongTaskIfNeeded();
        }
      });
    }).catch((err: BusinessError) => {
      this.isLongTaskStartPending = false;
      console.error(`Failed to create want agent: ${err.message}`);
      if (this.shouldKeepLongTask(generation)) {
        emitter.emit({ eventId: BG_EVENT_ID }, {
          data: { action: 'stopSensors' }
        });
      } else {
        this.restartLongTaskIfNeeded();
      }
    });
  }

  private shouldKeepLongTask(generation: number): boolean {
    return generation === this.longTaskGeneration &&
      this.isInBackground &&
      BackgroundState.isCollectionActive();
  }

  private stopLongTask(): void {
    if (!this.isLongTaskRunning || this.isLongTaskStopPending) {
      return;
    }
    this.isLongTaskStopPending = true;
    backgroundTaskManager.stopBackgroundRunning(this.context).then(() => {
      this.isLongTaskStopPending = false;
      this.isLongTaskRunning = false;
      this.longTaskStopRetryCount = 0;
      this.restartLongTaskIfNeeded();
    }).catch((err: BusinessError) => {
      this.isLongTaskStopPending = false;
      console.error(`Long task stop failed: ${err.message}`);
      if (this.longTaskStopRetryCount < 3 &&
        (!this.isInBackground || !BackgroundState.isCollectionActive())) {
        this.longTaskStopRetryCount += 1;
        const retryDelayMs = this.longTaskStopRetryCount * 300;
        setTimeout(() => {
          if (this.isLongTaskRunning && !this.isLongTaskStopPending &&
            (!this.isInBackground || !BackgroundState.isCollectionActive())) {
            this.stopLongTask();
          }
        }, retryDelayMs);
      }
    });
  }

  private updateKeepScreenOn(): void {
    if (!this.mainWindow) {
      return;
    }
    const keepScreenOn = !this.isInBackground && BackgroundState.isCollectionActive();
    this.mainWindow.setWindowKeepScreenOn(keepScreenOn).catch((screenError: BusinessError) => {
      console.warn(`Failed to update keep-screen-on state: ${screenError.message}`);
    });
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    if (!this.collectionHandlerRegistered) {
      emitter.on({ eventId: BG_EVENT_ID }, this.collectionEventHandler);
      this.collectionHandlerRegistered = true;
    }
    windowStage.loadContent('pages/Index', (err: BusinessError) => {
      if (err.code) {
        console.error(`Failed to load page. code=${err.code}, message=${err.message}`);
        return;
      }

      windowStage.getMainWindow((windowError: BusinessError, mainWindow: window.Window) => {
        if (windowError.code) {
          console.error(`Failed to get main window. code=${windowError.code}, message=${windowError.message}`);
          return;
        }
        this.mainWindow = mainWindow;

        mainWindow.setWindowSystemBarProperties({
          statusBarColor: '#0B1020',
          navigationBarColor: '#0B1020',
          isStatusBarLightIcon: true,
          isNavigationBarLightIcon: true
        }).catch((barError: BusinessError) => {
          console.warn(`Failed to set system bar style. code=${barError.code}, message=${barError.message}`);
        });

        this.updateKeepScreenOn();
      });
    });
  }

  onWindowStageDestroy(): void {
    this.isInBackground = false;
    this.longTaskGeneration += 1;
    this.stopLongTask();
    if (this.mainWindow) {
      this.mainWindow.setWindowKeepScreenOn(false).catch((screenError: BusinessError) => {
        console.warn(`Failed to release keep-screen-on state: ${screenError.message}`);
      });
      this.mainWindow = undefined;
    }
    if (this.collectionHandlerRegistered) {
      emitter.off(BG_EVENT_ID, this.collectionEventHandler);
      this.collectionHandlerRegistered = false;
    }
  }
}
