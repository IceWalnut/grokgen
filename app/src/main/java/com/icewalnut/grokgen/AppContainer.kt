package com.icewalnut.grokgen

import android.content.Context
import com.icewalnut.grokgen.data.SettingsStore
import com.icewalnut.grokgen.data.UploadRepository
import com.icewalnut.grokgen.net.GatewayApi
import com.icewalnut.grokgen.net.GatewayClient

/**
 * 手工装配，不用依赖注入框架。
 *
 * **为什么不用 Hilt/Koin**：整个 App 只有一个需要注入的东西（配好地址的 [GatewayApi]），
 * 而它的构造依赖一个**运行时才知道的值** —— 用户填的服务器地址。
 * 依赖注入框架在这种「依赖在运行时变化」的场景里反而要额外写作用域管理。
 *
 * ⚠️ **这条选择的代价要认**：App 长到 M4（加上 SD 生图页、GPU 页、媒体库页）时，
 * 手工装配可能会变难看。**那时再引入框架**，不要现在为了将来付复杂度。
 */
class AppContainer(context: Context) {

    private val appContext = context.applicationContext

    val settingsStore: SettingsStore = SettingsStore(appContext)

    /**
     * 按 base URL 现造一个 API 客户端。
     *
     * ⚠️ **不缓存。** 用户随时可能在连接页改地址，缓存一个旧地址的客户端
     * 会让「改了地址但没生效」变成一个很难查的问题 —— 而造一个 Retrofit 实例很便宜。
     */
    fun gatewayApi(baseUrl: String): GatewayApi = GatewayClient.create(baseUrl)

    /**
     * 上传用的仓库。
     *
     * ⚠️ 它拿到的是 [GatewayClient.createForUpload] 而不是 [gatewayApi] ——
     * 那个客户端的写超时是 60 秒。用健康检查那个（写超时 10 秒）的话，
     * 一条 12 MB 的上传在链路抖动时会超时，
     * **而用户看到的会是「服务器没有应答」—— 一个错误的诊断**。
     */
    val uploadRepository: UploadRepository =
        UploadRepository(appContext) { baseUrl -> GatewayClient.createForUpload(baseUrl) }
}
