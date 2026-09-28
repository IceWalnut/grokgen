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
import com.icewalnut.grokgen.ui.generate.GenerateScreen
import com.icewalnut.grokgen.ui.generate.GenerateViewModel
import com.icewalnut.grokgen.ui.theme.GrokgenTheme
import com.icewalnut.grokgen.ui.upload.UploadScreen
import com.icewalnut.grokgen.ui.upload.UploadViewModel

/**
 * 页面。
 *
 * ⚠️ **故意不引导航库。** 和 `AppContainer` 不引依赖注入框架是同一个理由：
 * **现在引，是为了将来付复杂度。**
 *
 * M2R3 加到第三个页面时看过一次，仍然不引：三个页面、回退关系是一条直线
 * （连接 ← 生成 ← 上传），没有深层回退栈，也没有页面之间传复杂参数 ——
 * 上传页把结果交回生成页，靠的是两个 ViewModel 都挂在这个 Activity 上。
 * M2R4 加队列页与任务详情（要带 `job_id` 跳转）时再看一次。
 */
private enum class Screen { Connect, Generate, Upload }

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
                                onOpenGenerate = { screen = Screen.Generate },
                            )
                        }

                        Screen.Generate -> {
                            BackHandler { screen = Screen.Connect }

                            val viewModel: GenerateViewModel = viewModel(
                                factory = GenerateViewModel.Factory(container),
                            )
                            val state by viewModel.state.collectAsStateWithLifecycle()

                            GenerateScreen(
                                state = state,
                                onFormChange = viewModel::updateForm,
                                onPickFirstFrame = { screen = Screen.Upload },
                                onSubmit = viewModel::submit,
                                onBack = { screen = Screen.Connect },
                            )
                        }

                        Screen.Upload -> {
                            BackHandler { screen = Screen.Generate }

                            val viewModel: UploadViewModel = viewModel(
                                factory = UploadViewModel.Factory(container),
                            )
                            val state by viewModel.state.collectAsStateWithLifecycle()
                            // ⚠️ 同一个 Activity 作用域 ⇒ 与生成页拿到的是同一个实例。
                            val generateViewModel: GenerateViewModel = viewModel(
                                factory = GenerateViewModel.Factory(container),
                            )

                            UploadScreen(
                                state = state,
                                onPicked = viewModel::onPicked,
                                onUpload = viewModel::upload,
                                onBack = { screen = Screen.Generate },
                                onUseImage = { image ->
                                    generateViewModel.setFirstFrame(image)
                                    screen = Screen.Generate
                                },
                            )
                        }
                    }
                }
            }
        }
    }
}
