################################################################################
# Automatically-generated file. Do not edit!
################################################################################

SHELL = cmd.exe

# Each subdirectory must supply rules for building sources it contributes
driverlib/MSP430FR5xx_6xx/%.obj: ../driverlib/MSP430FR5xx_6xx/%.c $(GEN_OPTS) | $(GEN_FILES) $(GEN_MISC_FILES)
	@echo 'MSP430 Compiler: "$<"'
	"C:/ti/ccs2100/ccs/tools/compiler/ti-cgt-msp430_21.6.2.LTS/bin/cl430" -vmspx --data_model=restricted --use_hw_mpy=F5 --include_path="C:/ti/ccs2100/ccs/ccs_base/msp430/include" --include_path="C:/Users/Oguzm/OneDrive - ozyegin.edu.tr/Desktop/Github_Projects/TrentoProjects/STFT_Parity" --include_path="C:/Users/Oguzm/OneDrive - ozyegin.edu.tr/Desktop/Github_Projects/TrentoProjects/STFT_Parity/driverlib/MSP430FR5xx_6xx" --include_path="C:/ti/ccs2100/ccs/tools/compiler/ti-cgt-msp430_21.6.2.LTS/include" --include_path="C:/Users/Oguzm/OneDrive - ozyegin.edu.tr/Desktop/Github_Projects/TrentoProjects/STFT_Parity/inc" --include_path="C:/ti/msp/DSPLib_1_30_00_02/include" --advice:power="none" --advice:hw_config=all --define=__MSP430FR5994__ --define=_MPU_ENABLE --define=DEPRECATED -g --printf_support=full --diag_warning=225 --diag_wrap=off --display_error_number --silicon_errata=CPU21 --silicon_errata=CPU22 --silicon_errata=CPU40 --preproc_with_compile --preproc_dependency="driverlib/MSP430FR5xx_6xx/$(basename $(<F)).d_raw" --obj_directory="driverlib/MSP430FR5xx_6xx" $(GEN_OPTS__FLAG) "$<"


