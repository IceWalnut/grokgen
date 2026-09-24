package com.icewalnut.grokgen

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.icewalnut.grokgen.ui.connect.ConnectScreen
import com.icewalnut.grokgen.ui.connect.ConnectViewModel
import com.icewalnut.grokgen.ui.theme.GrokgenTheme
import com.icewalnut.grokgen.ui.upload.UploadScreen
import com.icewalnut.grokgen.ui.upload.UploadViewModel

/**
 * 页面。
 *
 * ⚠️ **故意不引导航库。** 两个页面用一个枚举加 `BackHandler` 就够了 ——
 * 和 `AppContainer` 不引依赖注入框架是同一个理由：
 * **现在引，是为了将来付复杂度。** M2R3 加到第三个页面时再看。
 */
private enum class Screen { Connect, Upload }

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)

        val container = AppContainer(applicationContext)

        setContent {
            GrokgenTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    var screen by rememberSaveable { mutableStateOf(Screen.Connect) }

                    when (screen) {
                        Screen.Connect -> {
                            val viewModel: ConnectViewModel = viewModel(
                                factory = ConnectViewModel.Factory(container),
                            )
                            val state by viewModel.state.collectAsStateWithLifecycle()

                            ConnectScreen(
                                state = state,
                                onInputChange = viewModel::onInputChange,
                                onConnect = viewModel::connect,
                                onOpenUpload = { screen = Screen.Upload },
                            )
                        }

                        Screen.Upload -> {
                            BackHandler { screen = Screen.Connect }

                            val viewModel: UploadViewModel = viewModel(
                                factory = UploadViewModel.Factory(container),
                            )
                            val state by viewModel.state.collectAsStateWithLifecycle()

                            UploadScreen(
                                state = state,
                                onPicked = viewModel::onPicked,
                                onUpload = viewModel::upload,
                                onBack = { screen = Screen.Connect },
                            )
                        }
                    }
                }
            }
        }
    }
}
