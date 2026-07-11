# 重新生成 tailwind.css

`frontend/tailwind.css` 是预编译好的静态 Tailwind 产物，页面通过
`<link rel="stylesheet" href="tailwind.css">` 直接加载，不再使用
`cdn.tailwindcss.com` 现场编译。

只有在 `frontend/index.html` 里新增/修改了 Tailwind 类名之后，才需要重新生成。
步骤（Windows，无需 npm/Node 依赖树，只下载一个独立可执行文件）：

```powershell
cd frontend/build
curl.exe -sSL -o tailwindcss.exe https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-windows-x64.exe
./tailwindcss.exe -i input.css -o ../tailwind.css --config tailwind.config.js --minify
```

## 关于动态类名的 safelist

`index.html` 里有几处颜色类名是用 JS 模板字符串拼出来的（例如
`` `bg-${color}-600/20` ``，`color` 来自 `PLAT_COLORS` 数组），Tailwind 的静态扫描
识别不到这种拼接结果，所以在 `tailwind.config.js` 的 `safelist` 里手动列出了
这些组合。如果以后修改了 `PLAT_COLORS` 的颜色列表，记得同步更新
`tailwind.config.js` 里的 `safelist` 生成逻辑。
