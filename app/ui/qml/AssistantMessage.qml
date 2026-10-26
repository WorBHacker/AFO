import QtQuick

// An answer: who answered, the reasoning accordion, a status line (model loading), the text and code, a footer.
Column {
    id: answer
    property var row: ({})
    property int rowIndex: -1
    spacing: 12

    Row {
        spacing: 8
        Rectangle {
            width: 22; height: 22; radius: 6
            color: answer.row.local ? Theme.p.item_selected : Theme.p.accent
            Icon {
                anchors.centerIn: parent
                name: answer.row.local ? "cpu" : "sparkle"
                size: 13
                tint: answer.row.local ? Theme.p.text_soft : Theme.p.on_accent
            }
        }
        Text {
            text: answer.row.author || ""
            color: Theme.p.text
            font.family: Theme.uiFont
            font.pixelSize: 13
            font.weight: Font.DemiBold
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            visible: !!answer.row.streaming
            text: "· отвечает"
            color: Theme.p.muted
            font.family: Theme.uiFont
            font.pixelSize: 13
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    Text {
        visible: !!answer.row.notice
        text: answer.row.notice || ""
        color: Theme.p.faint
        font.family: Theme.uiFont
        font.pixelSize: 12
    }

    Thinking {
        visible: !!answer.row.thinking
        width: answer.width
        info: answer.row.thinking || ({})
        rowIndex: answer.rowIndex
    }

    Repeater {
        model: answer.row.segments || []
        delegate: Loader {
            required property var modelData
            width: answer.width
            sourceComponent: modelData.kind === "code" ? codeSegment : proseSegment
            onLoaded: item.info = Qt.binding(() => modelData)
        }
    }
    Component { id: proseSegment; Prose { width: parent ? parent.width : 0 } }
    Component { id: codeSegment; CodeBlock { width: parent ? parent.width : 0 } }

    Row {
        visible: !answer.row.streaming && !!answer.row.footer
        spacing: 8
        Text {
            text: answer.row.footer || ""
            color: Theme.p.faint
            font.family: Theme.uiFont
            font.pixelSize: 12
            anchors.verticalCenter: parent.verticalCenter
        }
        IconButton {
            iconName: "copy"
            tint: Theme.p.faint
            implicitWidth: 22
            implicitHeight: 22
            tip: "Копировать ответ"
            onClicked: App.copy(answer.row.markdown || "")
        }
    }
}
