#!/bin/bash

# Python 代码格式化脚本
# 用法:
#   ./format.sh          格式化整个项目
#   ./format.sh file.py  只格式化指定文件
#   ./format.sh --git    只格式化 git 中新增/修改的 .py 文件

if [ "$1" = "--git" ]; then
    # 获取 git 中新增(A)、修改(M)的 .py 文件（含 staged 和 unstaged）
    TARGET=$(git diff --name-only --diff-filter=AM HEAD -- '*.py'; git diff --name-only --cached --diff-filter=AM -- '*.py')
    TARGET=$(echo "$TARGET" | sort -u)
    if [ -z "$TARGET" ]; then
        echo "没有检测到 git 中新增或修改的 .py 文件"
        exit 0
    fi
else
    TARGET="${@:-.}"
fi

echo "🔧 开始格式化代码......................."
echo "   目标: $TARGET"

# 删除未使用的 import
echo "1. 删除未使用的 import..."
~/.local/bin/autoflake --in-place --remove-all-unused-imports -r $TARGET

# import 排序
echo "2. 排序 import..."
~/.local/bin/isort $TARGET

# 代码格式化
echo "3. 格式化代码..."
~/.local/bin/black $TARGET

echo "✅ 格式化完成!"
