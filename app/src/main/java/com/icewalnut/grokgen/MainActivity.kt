package com.icewalnut.grokgen

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.icewalnut.grokgen.ui.connect.ConnectScreen
import com.icewalnut.grokgen.ui.connect.ConnectViewModel
import com.icewalnut.grokgen.ui.theme.GrokgenTheme

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
                    val viewModel: ConnectViewModel = viewModel(
                        factory = ConnectViewModel.Factory(container),
                    )
                    val state by viewModel.state.collectAsStateWithLifecycle()

                    ConnectScreen(
                        state = state,
                        onInputChange = viewModel::onInputChange,
                        onConnect = viewModel::connect,
                    )
                }
            }
        }
    }
}
