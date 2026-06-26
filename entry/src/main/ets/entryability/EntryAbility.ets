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

  onBackground(): void {
    if (BackgroundState.recordingActive) {
      this.startLongTask();
      return;
    }
    emitter.emit({ eventId: BG_EVENT_ID }, {
      data: { action: 'stopSensors' }
    });
  }

  onForeground(): void {
    if (this.isLongTaskRunning) {
      backgroundTaskManager.stopBackgroundRunning(this.context).catch((err: BusinessError) => {
        console.error(`Long task stop failed: ${err.message}`);
      });
      this.isLongTaskRunning = false;
    }
    emitter.emit({ eventId: BG_EVENT_ID }, {
      data: { action: 'restoreSensors' }
    });
  }

  private startLongTask(): void {
    if (this.isLongTaskRunning) {
      return;
    }
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
      backgroundTaskManager.startBackgroundRunning(
        this.context,
        backgroundTaskManager.BackgroundMode.LOCATION,
        agent
      ).then(() => {
        this.isLongTaskRunning = true;
      }).catch((err: BusinessError) => {
        console.error(`Long task start failed: ${err.message}`);
        emitter.emit({ eventId: BG_EVENT_ID }, {
          data: { action: 'stopSensors' }
        });
      });
    }).catch((err: BusinessError) => {
      console.error(`Failed to create want agent: ${err.message}`);
      emitter.emit({ eventId: BG_EVENT_ID }, {
        data: { action: 'stopSensors' }
      });
    });
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
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

        mainWindow.setWindowSystemBarProperties({
          statusBarColor: '#0B1020',
          navigationBarColor: '#0B1020',
          isStatusBarLightIcon: true,
          isNavigationBarLightIcon: true
        }).catch((barError: BusinessError) => {
          console.warn(`Failed to set system bar style. code=${barError.code}, message=${barError.message}`);
        });

        mainWindow.setWindowKeepScreenOn(true).catch((screenError: BusinessError) => {
          console.warn(`Failed to keep screen on. code=${screenError.code}, message=${screenError.message}`);
        });
      });
    });
  }
}
