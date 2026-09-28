package com.icewalnut.grokgen

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.LaunchedEffect
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
import com.icewalnut.grokgen.ui.queue.JobDetailScreen
import com.icewalnut.grokgen.ui.queue.JobDetailViewModel
import com.icewalnut.grokgen.ui.queue.QueueScreen
import com.icewalnut.grokgen.ui.queue.QueueViewModel
import com.icewalnut.grokgen.ui.theme.GrokgenTheme
import com.icewalnut.grokgen.ui.upload.UploadScreen
import com.icewalnut.grokgen.ui.upload.UploadViewModel

/**
 * 页面。
 *
 * ⚠️ **故意不引导航库。** 和 `AppContainer` 不引依赖注入框架是同一个理由：
 * **现在引，是为了将来付复杂度。**
 *
 * M2R3 加到第三个页面时看过一次，仍然不引：回退关系是一条直线，
 * 上传页把结果交回生成页，靠的是两个 ViewModel 都挂在这个 Activity 上。
 *
 * M2R4 加到五个页面时又看了一次，**仍然不引**：
 * - 只有详情页要带一个参数（`job_id` 字符串），用一个 `rememberSaveable` 存着就够了；
 * - 回退关系仍是固定的树：连接 ← 生成 ←（上传 | 队列 ← 详情），
 *   详情一律回队列 —— 从生成页「查看进度」进来的也回队列，那正是用户下一步想看的地方。
 * 什么时候该引：出现「同一个页面可以从多处进入、且要回到来处」或深层链接时。
 */
private enum class Screen { Connect, Generate, Upload, Queue, Detail }

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
                    // 详情页看的是哪个任务。只在 screen == Detail 时有意义。
                    var detailJobId by rememberSaveable { mutableStateOf<String?>(null) }
                    val openJob: (String) -> Unit = { id ->
                        detailJobId = id
                        screen = Screen.Detail
                    }

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
                                onOpenQueue = { screen = Screen.Queue },
                                onOpenJob = openJob,
                            )
                        }

                        Screen.Queue -> {
                            BackHandler { screen = Screen.Generate }

                            val viewModel: QueueViewModel = viewModel(
                                factory = QueueViewModel.Factory(container),
                            )
                            // ⭐ collectAsStateWithLifecycle：App 退到后台时停止收集 ⇒ 轮询停止（VS-36）。
                            val state by viewModel.state.collectAsStateWithLifecycle()

                            QueueScreen(
                                state = state,
                                onOpenJob = openJob,
                                onNewJob = { screen = Screen.Generate },
                                onRefresh = viewModel::refresh,
                                onBack = { screen = Screen.Generate },
                            )
                        }

                        Screen.Detail -> {
                            BackHandler { screen = Screen.Queue }

                            val jobId = detailJobId
                            if (jobId == null) {
                                // 不该走到：进详情一定经过 openJob。真走到了就回队列，不显示一个空页面。
                                // 放在 LaunchedEffect 里 —— 组合过程中直接改状态是副作用。
                                LaunchedEffect(Unit) { screen = Screen.Queue }
                            } else {
                                // key 带上 jobId：换一个任务就是一个新的 ViewModel，不会显示上一个任务的状态。
                                val viewModel: JobDetailViewModel = viewModel(
                                    key = "job-detail-$jobId",
                                    factory = JobDetailViewModel.Factory(container, jobId),
                                )
                                val state by viewModel.state.collectAsStateWithLifecycle()

                                JobDetailScreen(
                                    jobId = jobId,
                                    state = state,
                                    onCancelClicked = viewModel::onCancelClicked,
                                    onDismissCancelConfirm = viewModel::dismissCancelConfirm,
                                    onBack = { screen = Screen.Queue },
                                )
                            }
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
