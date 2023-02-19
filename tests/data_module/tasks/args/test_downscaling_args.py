from climate_learn.data_module.tasks.args import DownscalingArgs
from climate_learn.data_module.data.args import DataArgs


class TestForecastingArgsInstantiation:
    def test_initialization(self):
        temp_data_args = DataArgs(variables=["random_variable_1"], split="Train")
        temp_highres_data_args = DataArgs(
            variables=["random_variable_2"], split="Train"
        )
        DownscalingArgs(
            temp_data_args,
            temp_highres_data_args,
            in_vars=["random_variable_1"],
            out_vars=["random_variable_2"],
        )
