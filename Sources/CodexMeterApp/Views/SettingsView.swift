import SwiftUI

struct SettingsView: View {
    @ObservedObject var store: MeterStore

    var body: some View {
        Form {
            Picker("Default graph view", selection: defaultViewBinding) {
                ForEach(ViewPreset.allCases) { preset in
                    Text(preset.label).tag(preset)
                }
            }
            .pickerStyle(.menu)

            LabeledContent("Archive", value: store.archivePath)

            HStack {
                Button("Open Archive") {
                    store.openArchiveFolder()
                }
                Button("Open Graph") {
                    store.openGraph()
                }
            }
        }
        .padding(24)
        .frame(width: 440)
    }

    private var defaultViewBinding: Binding<ViewPreset> {
        Binding(
            get: { store.settings.defaultViewPreset },
            set: { store.updateDefaultViewPreset($0) }
        )
    }
}
