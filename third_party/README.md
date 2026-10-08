# 第三方框架源码与补丁

[项目首页](../README.md) · [环境安装](../docs/setup.md)

这里记录外部框架的固定版本。框架源码由脚本恢复，不直接纳入主仓库；运行依赖安装在各框架自己的 Conda 环境。

| 文件 | 作用 |
| --- | --- |
| `lock.json` | 上游地址、固定 commit、补丁 SHA-256 |
| `patches/` | 项目对上游代码的修改及新增文件 |
| 恢复后的框架目录 | 本地 checkout，由 `.gitignore` 排除 |

**仅运行 Mock 或分析已有数据时可跳过本目录。** 采集真实框架时，恢复源码后还需安装对应环境；主环境不会自动安装这些依赖。

在仓库根目录执行：

```bash
# 预览需要恢复的源码，并检查补丁校验和
python scripts/setup_third_party.py --dry-run

# 恢复缺失的 checkout，检出固定 commit 并应用补丁
python scripts/setup_third_party.py
```

常用参数：`--dry-run` 只预览，`--root` 指定包含 `lock.json` 和补丁的目录（默认 `third_party`）。
脚本保留已有 checkout，不负责更新、创建 Conda 环境或安装依赖。后续步骤见[安装指南](../docs/setup.md)。
密钥和部署环境配置不进入补丁，排除项列在 `local_only_files`。更新框架时同步维护 commit、补丁及校验和，并运行对应适配器测试。
