#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极简视频剪辑工具 - 主程序入口
小白友好的视频剪辑软件

功能：
- 拖放视频文件
- 剪掉开头指定秒数
- 选择输出分辨率
- 显示处理进度
"""

import sys
import os

# 确保可以导入同目录下的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import VideoClipperApp


def main():
    """主函数"""
    # 创建并运行应用
    app = VideoClipperApp()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
