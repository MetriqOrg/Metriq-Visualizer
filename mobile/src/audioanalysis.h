#pragma once

#include <QMetaType>
#include <QVector>

namespace metriq::mobile {

struct AudioMetrics
{
    float rms = 0.0f;
    float zeroCrossingRate = 0.0f;
    float spectralCentroidHz = 0.0f;
    float spectralFlux = 0.0f;
    float onsetStrength = 0.0f;
};

class AudioAnalysis final
{
public:
    static AudioMetrics analyze(const QVector<float> &samples,
                                int sampleRate,
                                QVector<float> *previousSpectrum = nullptr);
};

} // namespace metriq::mobile

Q_DECLARE_METATYPE(metriq::mobile::AudioMetrics)
