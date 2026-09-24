// grokgen Android 客户端。
//
// ⚠️ 这里是单模块工程：根工程自己就是那个 Android 模块，没有 `app/app/` 那一层。
// 理由是 `Docs/Validation.md` 的 VS-30 / VS-41 与 App 架构文档 §3.1 都已经把
// 源码路径写成 `app/src/main/java`，保持一致比换个名字省事。

pluginManagement {
    repositories {
        google {
            content {
                includeGroupByRegex("com\\.android.*")
                includeGroupByRegex("com\\.google.*")
                includeGroupByRegex("androidx.*")
            }
        }
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "grokgen"
