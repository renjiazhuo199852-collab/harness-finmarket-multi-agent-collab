# Team Progress Documents

本目录用于团队成员提交个人研究资料、设计文档、阶段进度和周会材料。

## 目录规范

每位成员请创建一个以本人 **GitHub 用户名**命名的子目录：

```text
docs/team-progress/<github-username>/

示例：

docs/team-progress/example-user/

请勿将个人文档直接堆放在 docs/team-progress/ 根目录。

推荐个人目录结构
docs/team-progress/<github-username>/
├── README.md
├── progress/
├── design/
└── attachments/

成员可以按实际需要选择是否创建上述子目录。

文件命名

推荐格式：

YYYY-MM-DD-topic-version.ext

示例：

2026-07-24-fx-debate-progress-v1.md
2026-07-24-symbol-resolver-design-v1.pdf
2026-07-24-database-schema-v1.docx

推荐优先使用 Markdown 作为可审查的主文档；
Word、PowerPoint 和 PDF 可放入 attachments/ 作为附件。

提交规则
每位成员原则上只修改自己的 GitHub 用户名目录。
每次提交尽量只包含本人文档，不混入功能代码。
不修改其他成员目录，除非已取得对方同意。
不向本目录提交自动生成的临时文件、缓存或编辑器备份。
大文件上传前先与 Git 管理负责人确认。
文档中引用仓库文件时，优先使用相对路径。
修改完成后建议通过独立文档分支和 Pull Request 提交。
安全要求

禁止上传：

API Key、Token、Cookie、密码或私钥；
券商、模型服务或数据供应商凭证；
本机绝对路径和虚拟环境目录；
真实交易账户、持仓或敏感客户数据；
未经允许公开的内部业务资料；
包含个人敏感信息的日志和截图。

如发现敏感信息，应在提交前删除；不要依赖后续提交来覆盖。

推荐分支名
docs/<github-username>-<topic>

示例：

docs/example-user-weekly-progress
推荐提交信息
docs(progress): add <topic> report
当前目录负责人

本 README 仅定义文档入口和协作规则。

各团队成员自行维护本人 GitHub 用户名目录中的内容。
