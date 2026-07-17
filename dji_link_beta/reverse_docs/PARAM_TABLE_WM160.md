# WM160 (Mavic Mini 1) — Verified Valid Parameter Table

Captured live from the drone via DUML `0x03/0xF8` (read-by-hash), plaintext link.

**132 parameters answer on WM160** (of 686 names swept from `flyc_param_infos.json`). 
Only these 132 hashes return a value; the rest are silently absent on this airframe.


- Read: `0x03/0xF8 [hash u32 LE]` → reply `[retcode][hash u32 LE][value]`
- Write: `0x03/0xF9 [hash u32 LE][value]`, plaintext (cmd_type 0x40), immediate + self-persists
- `RW` = attribute bit0 set (accepts 0xF9 writes); `RO` = attribute 0 (0xF9 **silently dropped**); `+EE` = bit1 (persisted to EEPROM)
- Hash = `h=0; for b in fullname.encode('gbk'): h=(b+(h<<8)) % (2**32-5)`

| Parameter | Hash | Type | Access | Current | Min | Max | Default |
|---|---|---|---|---|---|---|---|
| `RC_STOP_MOTOR_TYPE_0` | 0x7a173d91 | u8 | RW+EE | 6 | 0 | 255 | 0 |
| `g_cfg_debug.follow_gimbal_yaw_when_watch_0` | 0x53a1ba94 | u8 | RW+EE | 1 | 0 | 1 | 0 |
| `g_cfg_debug.imu_cali_state[0][1]_0` | 0xf00b1958 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_cfg_debug.imu_cali_state[1][1]_0` | 0xf0101958 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_cfg_debug.imu_cali_state[2][1]_0` | 0xf0151958 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_cfg_debug.overshot_enable_0` | 0x89caf4f6 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_config.advanced_function.radius_limit_enabled_0` | 0x7ece6d19 | u8 | RW+EE | 1 | 0 | 1 | 0 |
| `g_config.aircraft.multi_rotor_type_0` | 0xbfef51d2 | u8 | RW+EE | 1 | 0 | 255 | 0 |
| `g_config.airport_limit_cfg.cfg_sim_disable_limit_0` | 0x3454b6b5 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.avoid_obstacle_limit_cfg.avoid_obstacle_enable_0` | 0x60fd01df | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.avoid_obstacle_limit_cfg.user_avoid_enable_0` | 0xb2e6a5e9 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.control.atti_vertical_0` | 0x36c21143 | s16 | RW+EE | 100 | 70 | 130 | 75 |
| `g_config.control.basic_pitch_0` | 0x695a0859 | s16 | RW+EE | 100 | 70 | 130 | 100 |
| `g_config.control.basic_roll_0` | 0xcd725af3 | s16 | RW+EE | 100 | 70 | 130 | 100 |
| `g_config.control.basic_tail_0` | 0xca7264ad | s16 | RW+EE | 100 | 70 | 130 | 75 |
| `g_config.control.brake_sensitivity_0` | 0x9eeedfaa | s16 | RW+EE | 80 | 70 | 130 | 100 |
| `g_config.control.control_mode[0]_0` | 0xde3b160b | u8 | RW+EE | 12 | 0 | 6 | 0 |
| `g_config.control.control_mode[1]_0` | 0xdf3b160b | u8 | RW+EE | 8 | 0 | 6 | 1 |
| `g_config.control.control_mode[2]_0` | 0xe03b160b | u8 | RW+EE | 7 | 0 | 6 | 2 |
| `g_config.control.rc_tilt_sensitivity_0` | 0x43224470 | s16 | RW+EE | 100 | 20 | 100 | 100 |
| `g_config.control.tilt_exp_mid_point_0` | 0x2b53609f | s16 | RW+EE | -13107 | 20 | 80 | 40 |
| `g_config.control.vert_vel_down_adding_0` | 0x1ebed955 | u8 | RW+EE | 0 | 0 | 5 | 0 |
| `g_config.control.yaw_exp_mid_point_0` | 0xf5fcaa49 | s16 | RW+EE | -26214 | 20 | 80 | 40 |
| `g_config.device.is_locked_0` | 0xa99b69e5 | u8 | RW | 0 | 0 | 1 | 0 |
| `g_config.engine.idle_level_0` | 0x53141284 | u8 | RW+EE | 10 | 1 | 15 | 5 |
| `g_config.engine.idle_time_0` | 0x3b532ef6 | u8 | RW+EE | cdcc8c3f | 1 | 5 | 2 |
| `g_config.engine.prop_auto_preload_0` | 0x88a90e16 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fail_safe.protect_action_0` | 0xcb653d71 | u8 | RW+EE | 2 | 0 | 2 | 2 |
| `g_config.fdi_open.ctrl_vibrate_fdi_open_0` | 0x9e5185f3 | u8 | RW+EE | 0 | 0 | 1 | 0 |
| `g_config.fdi_open.fit_open_0` | 0x5469c373 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_sensor[0].acc_bias_0` | 0x6efb3a17 | f32 | RW+EE | 0.0008 | 0 | 0.1 | 0 |
| `g_config.fdi_sensor[0].acc_stat_0` | 0x6efc8f4e | u8 | RW+EE | 7 | 0 | 255 | 0 |
| `g_config.fdi_sensor[0].gyr_bias_0` | 0xb9fbd23d | f32 | RW+EE | 0.0109 | 0 | 0.05 | 0 |
| `g_config.fdi_sensor[0].gyr_stat_0` | 0xb9fd2774 | u8 | RW+EE | 7 | 0 | 255 | 0 |
| `g_config.fdi_sensor[0].mag_over_0` | 0x86fba726 | f32 | RW+EE | 285.4398 | 0 | 1000 | 0 |
| `g_config.fdi_sensor[0].mag_stat_0` | 0x82fdbb1c | u8 | RW+EE | 5 | 0 | 255 | 0 |
| `g_config.fdi_sensor[1].acc_bias_0` | 0x6efb3a94 | f32 | RW+EE | 0.0 | 0 | 0.1 | 0 |
| `g_config.fdi_sensor[1].acc_stat_0` | 0x6efc8fcb | u8 | RW+EE | 1 | 0 | 255 | 0 |
| `g_config.fdi_sensor[1].gyr_bias_0` | 0xb9fbd2ba | f32 | RW+EE | 0.0 | 0 | 0.05 | 0 |
| `g_config.fdi_sensor[1].gyr_stat_0` | 0xb9fd27f1 | u8 | RW+EE | 1 | 0 | 255 | 0 |
| `g_config.fdi_sensor[1].mag_over_0` | 0x86fba7a3 | f32 | RW+EE | 0.0 | 0 | 1000 | 0 |
| `g_config.fdi_sensor[1].mag_stat_0` | 0x82fdbb99 | u8 | RW+EE | 1 | 0 | 255 | 0 |
| `g_config.fdi_sensor[2].acc_bias_0` | 0x6efb3b11 | f32 | RW+EE | 0.0 | 0 | 0.1 | 0 |
| `g_config.fdi_sensor[2].acc_stat_0` | 0x6efc9048 | u8 | RW+EE | 1 | 0 | 255 | 0 |
| `g_config.fdi_sensor[2].gyr_bias_0` | 0xb9fbd337 | f32 | RW+EE | 0.0 | 0 | 0.05 | 0 |
| `g_config.fdi_sensor[2].gyr_stat_0` | 0xb9fd286e | u8 | RW+EE | 1 | 0 | 255 | 0 |
| `g_config.fdi_sensor[2].mag_over_0` | 0x86fba820 | f32 | RW+EE | 0.0 | 0 | 1000 | 0 |
| `g_config.fdi_sensor[2].mag_stat_0` | 0x82fdbc16 | u8 | RW+EE | 1 | 0 | 255 | 0 |
| `g_config.fdi_switch.acc.by_fdi_0` | 0x32cafd0f | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.acc.default_index_0` | 0x2816e656 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.acc.random_test_0` | 0x27dddbf8 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.acc.with_fdi_0` | 0x6b554f88 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.baro.by_fdi_0` | 0x2306709d | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.baro.default_index_0` | 0xec8cb49b | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.baro.random_test_0` | 0x511fa06d | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.baro.with_fdi_0` | 0xdee300af | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.compass.by_fdi_0` | 0x3efd4255 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.compass.default_index_0` | 0x8747cf18 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.compass.random_test_0` | 0x23383b29 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.compass.with_fdi_0` | 0xb09b8c82 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.gps.by_fdi_0` | 0xca118d0f | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.gps.default_index_0` | 0x36dcca66 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.gps.random_test_0` | 0x88adeabc | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.gps.with_fdi_0` | 0xfb5843e6 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.gyro.by_fdi_0` | 0x7b067318 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.gyro.default_index_0` | 0x5c24b4dc | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.gyro.random_test_0` | 0x512c1005 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.gyro.with_fdi_0` | 0xe15fb8af | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.ns.by_fdi_0` | 0x96f0c9c6 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.ns.default_index_0` | 0xc4de9751 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.ns.random_test_0` | 0xe4dd78bc | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.fdi_switch.ns.with_fdi_0` | 0x380e4446 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.fdi_switch.open_0` | 0x5be2d2a3 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.flying_limit.max_height_0` | 0x0371238a | u16 | RW+EE | 500 | 15 | 500 | 120 |
| `g_config.flying_limit.max_radius_0` | 0x425c0a94 | u16 | RW+EE | 2000 | 15 | 5000 | 30 |
| `g_config.flying_limit.min_height_0` | 0x0438298a | u16 | RW+EE | 20 | 5 | 20 | 10 |
| `g_config.flying_limit.roof_limit_enable_0` | 0x32f1d5b4 | u8 | RW+EE | 0 | 0 | 1 | 0 |
| `g_config.flying_limit.user_avoid_ground_enable_0` | 0x1fca25eb | u8 | RW+EE | 1 | 0 | 1 | 0 |
| `g_config.gear_cfg.auto_control_enable_0` | 0x5d45e217 | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.gear_cfg.gear_func_en_0` | 0x5f6a490c | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_config.gear_cfg.hide_gear_en_0` | 0x58e51319 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_config.go_home.avoid_enable_0` | 0x9c044cca | u8 | RW+EE | 0 | 0 | 1 | 1 |
| `g_config.go_home.fixed_go_home_altitude_0` | 0x38cc63dc | u16 | RW+EE | 149 | 20 | 500 | 20 |
| `g_config.go_home.go_home_heading_option_0` | 0x6e280d61 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.gps_cfg.gps_enable_0` | 0xc5429582 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.gyr_acc_cfg.msc_require_side_0` | 0x6f9e60e8 | u8 | RW+EE | 62 | 0 | 255 | 0 |
| `g_config.misc_cfg.forearm_lamp_ctrl_0` | 0xedce59a2 | u8 | RW+EE | 255 | 0 | 256 | 1 |
| `g_config.misc_cfg.gimbal_priority_en_0` | 0xefdf16ad | u8 | RW+EE | 0 | 0 | 1 | 0 |
| `g_config.miss_rtk.use_rtk_data_0` | 0x4cd4784b | u8 | RW | 0 | 0 | 1 | 0 |
| `g_config.mode_normal_cfg.tilt_atti_range_0` | 0x95544807 | f32 | RW+EE | 20.0 | -360 | 360 | 0 |
| `g_config.mr_ctrl.prop_cover_en_0` | 0x02e8a1d5 | u8 | RW+EE | 0 | 0 | 1 | 0 |
| `g_config.mvo_cfg.mvo_func_en_0` | 0x97be0658 | u8 | RW+EE | 1 | 0 | 1 | 1 |
| `g_config.novice_cfg.novice_func_enabled_0` | 0xde9b1b7b | u8 | RW+EE | 0 | 0 | 1 | 0 |
| `g_config.topology_verify.single_mult_controller_0` | 0x66e5d569 | u8 | RW+EE | 0 | 0 | 1 | 0 |
| `g_config.voltage.battery_cell_0` | 0x58b4c372 | u8 | RW+EE | 2 | 2 | 6 | 6 |
| `g_config.voltage.level_1_protect_0` | 0xfb29d937 | u16 | RW+EE | 3800 | 6900 | 26000 | 22000 |
| `g_config.voltage.level_1_protect_type_0` | 0x41a4e115 | u8 | RW+EE | 0 | 0 | 1 | 0 |
| `g_config.voltage.level_2_protect_0` | 0xfb42d937 | u16 | RW+EE | 3600 | 6900 | 26000 | 21600 |
| `g_config.voltage.level_2_protect_type_0` | 0xbea4e115 | u8 | RW+EE | 2 | 0 | 2 | 2 |
| `g_config.voltage2.level2_smart_battert_land_0` | 0x79e5fb9b | u8 | RW+EE | 5 | 0 | 100 | 10 |
| `g_config.voltage2.level_1_function_0` | 0xc214cd92 | u8 | RW+EE | 0 | 0 | 10 | 0 |
| `g_config.voltage2.level_1_voltage_0` | 0x5aae5bcd | u8 | RW+EE | 20 | 0 | 100 | 30 |
| `g_config.voltage2.level_2_function_0` | 0xdb14cd92 | u8 | RW+EE | 2 | 0 | 10 | 1 |
| `g_config.voltage2.level_2_voltage_0` | 0x5ac75bcd | u8 | RW+EE | 5 | 0 | 100 | 10 |
| `g_config.voltage2.user_set_smart_bat_0` | 0xe2c0cd6f | u8 | RW+EE | 2 | 0 | 10 | 2 |
| `g_status.acc_gyro[0].cali_cnt_0` | 0xb29254ea | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_status.acc_gyro[0].state_0` | 0x11544d3d | t4 | RW+EE | 00 | 0 | 255 | 0 |
| `g_status.acc_gyro[0].temp_ready_0` | 0x03e5c864 | u8 | RW+EE | 1 | 0 | 255 | 0 |
| `g_status.acc_gyro[1].cali_cnt_0` | 0xb2925567 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_status.acc_gyro[1].state_0` | 0x1154663d | t4 | RW+EE | 00 | 0 | 255 | 0 |
| `g_status.acc_gyro[1].temp_ready_0` | 0x0462c864 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_status.acc_gyro[2].cali_cnt_0` | 0xb29255e4 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_status.acc_gyro[2].state_0` | 0x11547f3d | u8 | RW+EE | 0 | 4 | 255 | 0 |
| `g_status.acc_gyro[2].temp_ready_0` | 0x04dfc864 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_status.all_gyr_acc.cali_cnt_0` | 0x39c5ca21 | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_status.all_gyr_acc.cali_state_0` | 0xdfa79daa | u8 | RW+EE | 0 | -128 | 127 | 0 |
| `g_status.all_gyr_acc.msc_current_side_0` | 0x489a8c54 | u8 | RW+EE | 0 | 0 | 3 | 0 |
| `g_status.all_gyr_acc.msc_sampled_side_0` | 0x7b7100aa | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_status.all_gyr_acc.need_cali_type_0` | 0xf6ec96be | u8 | RW+EE | 0 | 0 | 255 | 0 |
| `g_status.ns_busy_dev_0` | 0x2873530b | u16 | RW+EE | 1365 | 0 | 65535 | 0 |
| `g_status.topology_verify.user_interface.imu_status_0` | 0x929097ef | u8 | RW+EE | 1 | 0 | 6 | 1 |
| `g_status.topology_verify.user_interface.mag_status_0` | 0xf56339ef | u8 | RW+EE | 1 | 0 | 4 | 1 |
| `g_status.user_info.statistical_info.total_motor_start_time_0` | 0x14633705 | f32 | RW+EE | 57933.3984 | 0 | 250 | 250 |
| `g_status.user_info.statistical_info_last.total_motor_start_time_0` | 0x17842e67 | f32 | RW+EE | 0.0 | 0 | 250 | 250 |
| `imu_app_temp_cali.cali_cnt_0` | 0xcc8ec761 | u8 | RO | 0 | 0 | 255 | 0 |
| `imu_app_temp_cali.start_flag_0` | 0xc46223b9 | u8 | RW | 0 | 0 | 127 | 0 |
| `imu_app_temp_cali.state_0` | 0x43d19856 | t4 | RO | 00 | 0 | 127 | 0 |
| `imu_app_temp_cali.temp_ready_0` | 0x765d4a50 | u8 | RO | 1 | 0 | 255 | 0 |
| `mass_center_calibrated_0` | 0xf3edc169 | u8 | RW+EE | 0 | 0 | 1 | 0 |
| `mode_sport_cfg_tilt_atti_range_0` | 0x3bf365ce | f32 | RW+EE | 30.0 | 5 | 40 | 5 |
| `mode_sport_cfg_vert_vel_up_0` | 0xac320b0d | f32 | RW+EE | 4.0 | 1 | 10 | 5 |
| `prop_cover_limit_enable_0` | 0x9d032c32 | u8 | RW+EE | 0 | 0 | 1 | 1 |
